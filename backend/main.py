"""
main.py - REST API for the forwarding-chain dashboard.

Upload a photo or video -> the backend decodes the chain hidden in it
(LSB/chain_imei.py: RGB pixel LSB in PNG photos, a hidden box in MP4 videos),
stores it, and merges it with every other copy of the same original (same
Image ID) into a forwarding tree. JPEGs are accepted but can't carry a chain
(a stamped photo is always a PNG), so they are listed as "no chain found".

Run:   uvicorn main:app --reload            (from the backend/ folder)
Docs:  http://127.0.0.1:8000/docs           (interactive, try uploads there)

Endpoints
---------
GET    /api/health
POST   /api/upload                    multipart "file" (PNG / JPEG / MP4 / MOV / 3GP)
GET    /api/uploads[?image_id=...]    list uploads
GET    /api/uploads/{id}              one upload with its chain
GET    /api/uploads/{id}/file         the uploaded photo or video
DELETE /api/uploads/{id}
GET    /api/images                    one row per Image ID (original photo/video)
GET    /api/images/{image_id}/tree    merged forwarding tree
GET    /api/devices                   every device seen, with its label
GET    /api/devices/{device_id}       one device + the photos it handled
PUT    /api/devices/{device_id}       set a label / notes
"""

import hashlib
import os
import sqlite3
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import db
from tree import build_tree

# The decoding code lives in the LSB technique folder.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "LSB"))
import chain_imei  # noqa: E402

# Videos can be large; the upload is streamed to disk, never held in memory.
MAX_UPLOAD_BYTES = int(os.environ.get("CHAIN_MAX_UPLOAD_MB", "1024")) * 1024 * 1024
CORS_ORIGINS = os.environ.get(
    "CHAIN_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    yield


app = FastAPI(title="Image Forwarding Chain API", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS,
                   allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _labels(con: sqlite3.Connection) -> dict:
    return {r["device_id"]: r["label"]
            for r in con.execute("SELECT device_id, label FROM devices WHERE label IS NOT NULL")}


def _hops(con: sqlite3.Connection, upload_id: int) -> list:
    return [dict(r) for r in con.execute(
        "SELECT position, device_id, imei, timestamp, role FROM hops "
        "WHERE upload_id = ? ORDER BY position", (upload_id,))]


def _upload(con: sqlite3.Connection, upload_id: int) -> dict:
    row = con.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Upload not found.")
    up = dict(row)
    up["found"] = bool(up["found"])
    labels = _labels(con)
    up["hops"] = [dict(h, label=labels.get(h["device_id"]) or h["imei"] or h["device_id"][:8])
                  for h in _hops(con, upload_id)]
    return up


def _tree(con: sqlite3.Connection, image_id: str) -> dict:
    rows = con.execute(
        "SELECT id, filename, media, uploaded_at FROM uploads WHERE image_id = ? "
        "ORDER BY uploaded_at, id", (image_id,)).fetchall()
    if not rows:
        raise HTTPException(404, "No uploads with this image ID.")
    uploads = [dict(r, hops=_hops(con, r["id"])) for r in rows]
    tree = build_tree(image_id, uploads, _labels(con))
    tree["media"] = rows[0]["media"]
    return tree


VIDEO_EXT_BY_BRAND = {b"qt  ": ".mov", b"3gp": ".3gp", b"3g2": ".3g2"}


def _save_upload(file: UploadFile) -> tuple:
    """
    Stream the upload to a temporary file (videos can be large), hashing it
    on the way. Returns (temp_path, sha256_hex, size, media, ext).
    """
    fd, tmp = tempfile.mkstemp(suffix=".upload", dir=db.DATA_DIR)
    h = hashlib.sha256()
    size = 0
    head = b""
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := file.file.read(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, f"File too large (max {MAX_UPLOAD_BYTES // 2**20} MB).")
                if len(head) < 12:
                    head += chunk[:12]
                h.update(chunk)
                out.write(chunk)
    except BaseException:
        os.remove(tmp)
        raise

    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        media, ext = "image", ".png"
    elif head.startswith(b"\xff\xd8\xff"):
        media, ext = "image", ".jpg"
    elif head[4:8] == b"ftyp":
        brand = head[8:12]
        media = "video"
        ext = next((e for b, e in VIDEO_EXT_BY_BRAND.items() if brand.startswith(b)), ".mp4")
    else:
        os.remove(tmp)
        raise HTTPException(415, "Unsupported file. Upload a PNG (or JPEG) photo "
                                 "or an MP4 / MOV / 3GP video.")
    return tmp, h.hexdigest(), size, media, ext


def _decode(path: str, media: str) -> Optional[chain_imei.ReadResult]:
    try:
        return chain_imei.read_chain(path)
    except (ValueError, OSError) as e:
        kind = "photo" if media == "image" else "video"
        raise HTTPException(422, f"Could not read this {kind}: {e}") from e


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/upload")
def upload(file: UploadFile = File(...)):
    tmp, sha, size, media, ext = _save_upload(file)
    filename = Path(file.filename or f"upload{ext}").name
    try:
        with db.connect() as con:
            row = con.execute("SELECT id FROM uploads WHERE sha256 = ?", (sha,)).fetchone()
            if row:
                up = _upload(con, row["id"])
                return {"duplicate": True, "upload": up,
                        "tree": _tree(con, up["image_id"]) if up["found"] else None}

        res = _decode(tmp, media)            # slow part: outside the DB lock

        with db.connect() as con:
            cur = con.execute(
                "INSERT INTO uploads (sha256, filename, media, ext, size_bytes, uploaded_at, "
                "found, image_id, confidence, hop_count, bits_per_pixel) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sha, filename, media, ext, size, db.now_iso(), int(res is not None),
                 res.chain.image_id.hex() if res else None,
                 round(res.confidence, 4) if res else None,
                 len(res.chain.hops) if res else 0,
                 res.bits_per_pixel if res else None))
            upload_id = cur.lastrowid
            if res:
                con.executemany(
                    "INSERT INTO hops (upload_id, position, device_id, imei, timestamp, role) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [(upload_id, i, h["device_id"], h["imei"], h["timestamp"], h["role"])
                     for i, h in enumerate((h.to_dict() for h in res.chain.hops), 1)])
            os.replace(tmp, _stored_path(sha, ext))
            up = _upload(con, upload_id)
            return {"duplicate": False, "upload": up,
                    "tree": _tree(con, up["image_id"]) if res else None}
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _stored_path(sha: str, ext: str) -> Path:
    return db.FILES_DIR / f"{sha}{ext}"


MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".mp4": "video/mp4", ".mov": "video/quicktime",
               ".3gp": "video/3gpp", ".3g2": "video/3gpp2"}


@app.get("/api/uploads")
def list_uploads(image_id: Optional[str] = None):
    with db.connect() as con:
        sql = ("SELECT id, filename, media, size_bytes, uploaded_at, found, image_id, "
               "confidence, hop_count, bits_per_pixel FROM uploads")
        args = ()
        if image_id:
            sql += " WHERE image_id = ?"
            args = (image_id,)
        rows = con.execute(sql + " ORDER BY id DESC", args).fetchall()
        return [dict(r, found=bool(r["found"])) for r in rows]


@app.get("/api/uploads/{upload_id}")
def get_upload(upload_id: int):
    with db.connect() as con:
        return _upload(con, upload_id)


@app.get("/api/uploads/{upload_id}/file")
def get_upload_file(upload_id: int):
    with db.connect() as con:
        row = con.execute("SELECT sha256, filename, ext FROM uploads WHERE id = ?",
                          (upload_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Upload not found.")
    path = _stored_path(row["sha256"], row["ext"])
    if not path.exists():
        raise HTTPException(404, "File missing on the server.")
    # inline (not attachment) so the dashboard can show it in <img> / <video>
    return FileResponse(path, media_type=MEDIA_TYPES.get(row["ext"], "application/octet-stream"),
                        filename=row["filename"], content_disposition_type="inline")


@app.delete("/api/uploads/{upload_id}")
def delete_upload(upload_id: int):
    with db.connect() as con:
        row = con.execute("SELECT sha256, ext FROM uploads WHERE id = ?", (upload_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Upload not found.")
        con.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
    _stored_path(row["sha256"], row["ext"]).unlink(missing_ok=True)
    return {"deleted": upload_id}


@app.get("/api/images")
def list_images():
    with db.connect() as con:
        rows = con.execute("""
            SELECT u.image_id,
                   COUNT(DISTINCT u.id)        AS copies,
                   COUNT(DISTINCT h.device_id) AS devices,
                   MAX(u.hop_count)            AS max_hops,
                   MIN(h.timestamp)            AS first_hop_at,
                   MAX(u.uploaded_at)          AS last_upload_at,
                   MAX(u.id)                   AS latest_upload_id,
                   MAX(u.media)                AS media
            FROM uploads u JOIN hops h ON h.upload_id = u.id
            WHERE u.found = 1
            GROUP BY u.image_id
            ORDER BY last_upload_at DESC""").fetchall()
        labels = _labels(con)
        out = []
        for r in rows:
            origin = con.execute(
                "SELECT h.device_id, h.imei FROM hops h JOIN uploads u ON u.id = h.upload_id "
                "WHERE u.image_id = ? AND h.position = 1 ORDER BY h.timestamp LIMIT 1",
                (r["image_id"],)).fetchone()
            out.append(dict(r, origin={
                "device_id": origin["device_id"], "imei": origin["imei"],
                "label": labels.get(origin["device_id"]) or origin["imei"]
                         or origin["device_id"][:8]}))
        return out


@app.get("/api/images/{image_id}/tree")
def get_tree(image_id: str):
    with db.connect() as con:
        return _tree(con, image_id.lower())


@app.get("/api/devices")
def list_devices():
    with db.connect() as con:
        rows = con.execute("""
            SELECT h.device_id,
                   MAX(h.imei)                 AS imei,
                   d.label, d.notes,
                   COUNT(DISTINCT u.image_id)  AS images,
                   MIN(h.timestamp)            AS first_seen,
                   MAX(h.timestamp)            AS last_seen
            FROM hops h JOIN uploads u ON u.id = h.upload_id
            LEFT JOIN devices d ON d.device_id = h.device_id
            GROUP BY h.device_id
            ORDER BY last_seen DESC""").fetchall()
        return [dict(r) for r in rows]


@app.get("/api/devices/{device_id}")
def get_device(device_id: str):
    device_id = device_id.lower()
    with db.connect() as con:
        meta = con.execute("SELECT label, notes, updated_at FROM devices WHERE device_id = ?",
                           (device_id,)).fetchone()
        rows = con.execute("""
            SELECT u.image_id, MAX(h.imei) AS imei, MIN(h.position) AS position,
                   MIN(h.timestamp) AS first_seen, COUNT(DISTINCT u.id) AS copies
            FROM hops h JOIN uploads u ON u.id = h.upload_id
            WHERE h.device_id = ?
            GROUP BY u.image_id ORDER BY first_seen DESC""", (device_id,)).fetchall()
    if meta is None and not rows:
        raise HTTPException(404, "Device never seen.")
    return {"device_id": device_id, **(dict(meta) if meta else {"label": None, "notes": None}),
            "imei": next((r["imei"] for r in rows if r["imei"]), None),
            "images": [dict(r) for r in rows]}


class DeviceUpdate(BaseModel):
    label: Optional[str] = Field(None, max_length=80)
    notes: Optional[str] = Field(None, max_length=1000)


@app.put("/api/devices/{device_id}")
def update_device(device_id: str, body: DeviceUpdate):
    device_id = device_id.lower()
    if len(device_id) != 16 or any(c not in "0123456789abcdef" for c in device_id):
        raise HTTPException(422, "device_id must be 16 hex characters.")
    with db.connect() as con:
        con.execute(
            "INSERT INTO devices (device_id, label, notes, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(device_id) DO UPDATE SET label = excluded.label, "
            "notes = excluded.notes, updated_at = excluded.updated_at",
            (device_id, body.label or None, body.notes or None, db.now_iso()))
    return get_device(device_id)

#!/usr/bin/env python3
"""
test_api.py - End-to-end tests for the backend, using a throw-away database.

Builds this forwarding tree with the real chain code, uploads the copies
held by C and D, and checks what the API returns:

        A
       / \\
      B   D        (C and D claim the photo on arrival)
      |
      C

Usage:  python test_api.py          (from the backend/ folder)
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

TMP = tempfile.mkdtemp(prefix="chain_api_test_")
os.environ["CHAIN_DATA_DIR"] = TMP          # must be set before importing db

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "LSB"))

from fastapi.testclient import TestClient  # noqa: E402

import chain_imei as ci  # noqa: E402
import chain_payload as cp  # noqa: E402
from main import app  # noqa: E402

IMEI = {"A": "356938035643809", "B": "490154203237518",
        "C": "352099001761481", "D": "013467007045768"}
DEV = {k: cp.parse_device_id(f"phone-{k}") for k in IMEI}
failures = 0


def check(name: str, cond: bool) -> None:
    global failures
    print(f"  [{'OK' if cond else 'FAIL'}] {name}")
    if not cond:
        failures += 1


def p(name: str) -> str:
    return os.path.join(TMP, name)


def make_photos() -> None:
    rng = np.random.default_rng(3)
    h, w = 768, 1024
    y, x = np.mgrid[0:h, 0:w]
    img = np.dstack([x * 255 // w, y * 255 // h, (x + y) * 255 // (w + h)])
    img = img.astype(np.int16) + rng.integers(-30, 30, (h, w, 3))
    img = cv2.GaussianBlur(np.clip(img, 0, 255).astype(np.uint8), (3, 3), 0)
    cv2.imwrite(p("cover.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 90])

    t = 1792500000
    ci.append_hop(p("cover.jpg"), p("A.jpg"), DEV["A"], IMEI["A"], timestamp=t)
    ci.append_hop(p("A.jpg"), p("AB.jpg"), DEV["B"], IMEI["B"], timestamp=t + 60)
    ci.append_hop(p("AB.jpg"), p("ABC.jpg"), DEV["C"], IMEI["C"], cp.ROLE_CLAIM, t + 120)
    ci.append_hop(p("A.jpg"), p("AD.jpg"), DEV["D"], IMEI["D"], cp.ROLE_CLAIM, t + 90)
    cv2.imwrite(p("recompressed.jpg"), cv2.imread(p("ABC.jpg")),
                [cv2.IMWRITE_JPEG_QUALITY, 85])
    cv2.imwrite(p("photo.png"), img)


def make_videos() -> None:
    """A short lossy H.264 MP4, stamped A -> B -> C and A -> D."""
    import av
    rng = np.random.default_rng(5)
    out = av.open(p("plain.mp4"), "w")
    s = out.add_stream("libx264", rate=25)
    s.width, s.height, s.pix_fmt = 320, 240, "yuv420p"
    base = rng.integers(0, 256, (240, 320, 3), dtype=np.uint8)
    for i in range(25):
        out.mux(s.encode(av.VideoFrame.from_ndarray(np.roll(base, i * 3, axis=1), format="rgb24")))
    out.mux(s.encode())
    out.close()

    t = 1792600000
    ci.append_hop(p("plain.mp4"), p("vA.mp4"), DEV["A"], IMEI["A"], timestamp=t)
    ci.append_hop(p("vA.mp4"), p("vAB.mp4"), DEV["B"], IMEI["B"], timestamp=t + 60)
    ci.append_hop(p("vAB.mp4"), p("vABC.mp4"), DEV["C"], IMEI["C"], cp.ROLE_CLAIM, t + 120)
    ci.append_hop(p("vA.mp4"), p("vAD.mp4"), DEV["D"], IMEI["D"], cp.ROLE_CLAIM, t + 90)


def check_migration() -> None:
    import sqlite3
    import db
    old_dir = Path(TMP) / "old"
    old_dir.mkdir()
    con = sqlite3.connect(old_dir / "chain.db")
    con.executescript("""
        CREATE TABLE uploads (id INTEGER PRIMARY KEY AUTOINCREMENT, sha256 TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL, size_bytes INTEGER NOT NULL, uploaded_at TEXT NOT NULL,
            found INTEGER NOT NULL, image_id TEXT, confidence REAL, hop_count INTEGER NOT NULL DEFAULT 0);
        INSERT INTO uploads (sha256, filename, size_bytes, uploaded_at, found)
            VALUES ('abc', 'old.jpg', 10, '2026-01-01T00:00:00Z', 0);""")
    con.commit()
    con.close()
    saved = db.DB_PATH, db.FILES_DIR
    db.DB_PATH, db.FILES_DIR = old_dir / "chain.db", old_dir / "uploads"
    try:
        db.init()
        with db.connect() as c:
            row = dict(c.execute("SELECT media, ext FROM uploads").fetchone())
    finally:
        db.DB_PATH, db.FILES_DIR = saved
    check("old rows get media = image, ext = .jpg", row == {"media": "image", "ext": ".jpg"})


def upload(client, name: str):
    with open(p(name), "rb") as f:
        return client.post("/api/upload", files={"file": (name, f, "image/jpeg")})


def labels_of(node) -> list:
    return [c["label"] for c in node["children"]]


def main() -> int:
    make_photos()
    with TestClient(app) as client:
        print("1. Health")
        check("GET /api/health", client.get("/api/health").json() == {"status": "ok"})

        print("2. Upload C's copy (A -> B -> C)")
        r = upload(client, "ABC.jpg")
        body = r.json()
        check("200 OK", r.status_code == 200)
        check("chain found", body["upload"]["found"])
        check("hops are A, B, C",
              [h["imei"] for h in body["upload"]["hops"]] == [IMEI["A"], IMEI["B"], IMEI["C"]])
        image_id = body["upload"]["image_id"]
        check("tree returned with 3 devices", body["tree"]["stats"]["devices"] == 3)

        print("3. Upload D's copy (A -> D): branches off A")
        body = upload(client, "AD.jpg").json()
        check("same image ID as C's copy", body["upload"]["image_id"] == image_id)
        tree = client.get(f"/api/images/{image_id}/tree").json()
        root = tree["root"]
        check("one origin (A)", labels_of(root) == [IMEI["A"]])
        a = root["children"][0]
        check("A has children B, D (sorted by time)", labels_of(a) == [IMEI["B"], IMEI["D"]])
        b = next(c for c in a["children"] if c["imei"] == IMEI["B"])
        check("B -> C", labels_of(b) == [IMEI["C"]])
        check("C is a claim holding 1 copy",
              b["children"][0]["role"] == "claim" and len(b["children"][0]["copies"]) == 1)
        check("flat lists: 5 nodes, 4 edges",
              len(tree["nodes"]) == 5 and len(tree["edges"]) == 4)
        check("stats: 2 copies, 4 devices, depth 3",
              tree["stats"] == {"copies": 2, "devices": 4, "max_depth": 3})
        check("no anomalies", tree["anomalies"] == [])

        print("4. Duplicates and bad files")
        body = upload(client, "ABC.jpg").json()
        check("same file again is a duplicate", body["duplicate"])
        check("still 2 uploads", len(client.get("/api/uploads").json()) == 2)
        r = upload(client, "cover.jpg")
        check("unstamped JPEG: found = false",
              r.status_code == 200 and not r.json()["upload"]["found"] and r.json()["tree"] is None)
        r = upload(client, "recompressed.jpg")
        check("re-compressed copy: found = false", not r.json()["upload"]["found"])
        r = upload(client, "photo.png")
        check("PNG rejected with 415", r.status_code == 415)
        r = client.post("/api/upload", files={"file": ("x.jpg", b"\xff\xd8\xff garbage", "image/jpeg")})
        check("corrupt JPEG rejected with 422", r.status_code == 422)

        print("5. Images and devices")
        images = client.get("/api/images").json()
        check("one image listed", len(images) == 1 and images[0]["image_id"] == image_id)
        check("image summary: 2 copies, 4 devices, origin A",
              images[0]["copies"] == 2 and images[0]["devices"] == 4
              and images[0]["origin"]["imei"] == IMEI["A"])
        devices = client.get("/api/devices").json()
        check("4 devices listed", len(devices) == 4)

        dev_a = DEV["A"].hex()
        r = client.put(f"/api/devices/{dev_a}", json={"label": "Dev's phone"})
        check("label a device", r.status_code == 200 and r.json()["label"] == "Dev's phone")
        tree = client.get(f"/api/images/{image_id}/tree").json()
        check("label shows in the tree", labels_of(tree["root"]) == ["Dev's phone"])
        check("device detail lists the image",
              client.get(f"/api/devices/{dev_a}").json()["images"][0]["image_id"] == image_id)
        check("bad device ID rejected",
              client.put("/api/devices/xyz", json={"label": "x"}).status_code == 422)

        print("6. Files and deletion")
        first = client.get("/api/uploads").json()[-1]
        r = client.get(f"/api/uploads/{first['id']}/file")
        check("download the uploaded photo",
              r.status_code == 200 and r.content == open(p("ABC.jpg"), "rb").read())
        client.delete(f"/api/uploads/{first['id']}")
        tree = client.get(f"/api/images/{image_id}/tree").json()
        check("after deleting C's copy the tree is A -> D only",
              tree["stats"] == {"copies": 1, "devices": 2, "max_depth": 2})
        check("unknown upload gives 404", client.get("/api/uploads/9999").status_code == 404)

        print("7. Videos")
        make_videos()
        r = upload(client, "vABC.mp4")
        body = r.json()
        check("video upload: 200 and chain found",
              r.status_code == 200 and body["upload"]["found"]
              and body["upload"]["media"] == "video")
        check("video hops are A, B, C",
              [h["imei"] for h in body["upload"]["hops"]] == [IMEI["A"], IMEI["B"], IMEI["C"]])
        vid = body["upload"]["image_id"]
        check("video has its own image ID (separate tree from the photo)", vid != image_id)
        upload(client, "vAD.mp4")
        tree = client.get(f"/api/images/{vid}/tree").json()
        check("video tree: A branches to B and D",
              labels_of(tree["root"]) == ["Dev's phone"]
              and labels_of(tree["root"]["children"][0]) == [IMEI["B"], IMEI["D"]])
        check("tree reports media = video", tree["media"] == "video")
        images = {i["image_id"]: i for i in client.get("/api/images").json()}
        check("images list marks photo and video",
              images[vid]["media"] == "video" and images[image_id]["media"] == "image")
        up_id = body["upload"]["id"]
        r = client.get(f"/api/uploads/{up_id}/file")
        check("download video: correct type and bytes",
              r.headers["content-type"] == "video/mp4"
              and r.content == open(p("vABC.mp4"), "rb").read())
        r = client.get(f"/api/uploads/{up_id}/file", headers={"Range": "bytes=0-99"})
        check("video supports seeking (HTTP 206 range request)",
              r.status_code == 206 and len(r.content) == 100)
        check("plain video: found = false", not upload(client, "plain.mp4").json()["upload"]["found"])
        r = client.post("/api/upload", files={"file": ("x.mp4", b"\x00\x00\x00\x18ftypisom" + b"\x00" * 4,
                                                        "video/mp4")})
        check("corrupt MP4 rejected with 422", r.status_code == 422)

    print("8. Upgrading a database from before video support")
    check_migration()

    shutil.rmtree(TMP, ignore_errors=True)
    print()
    print("ALL API TESTS PASSED" if failures == 0 else f"{failures} API TEST(S) FAILED")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

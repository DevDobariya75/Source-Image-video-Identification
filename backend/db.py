"""
db.py - SQLite storage for uploaded photos, their chains and known devices.

Tables
------
uploads : one row per distinct uploaded photo or video (deduplicated by SHA-256)
hops    : the decoded chain of each upload, one row per hop
devices : optional labels for device IDs ("Dev's phone"), set from the dashboard
"""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("CHAIN_DATA_DIR",
                               Path(__file__).resolve().parent / "data"))
DB_PATH = DATA_DIR / "chain.db"
FILES_DIR = DATA_DIR / "uploads"

SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256       TEXT NOT NULL UNIQUE,
    filename     TEXT NOT NULL,
    media        TEXT NOT NULL DEFAULT 'image',   -- 'image' | 'video'
    ext          TEXT NOT NULL DEFAULT '.jpg',    -- extension of the stored file
    size_bytes   INTEGER NOT NULL,
    uploaded_at  TEXT NOT NULL,
    found        INTEGER NOT NULL,          -- 1 if a valid chain was decoded
    image_id     TEXT,                      -- chain image ID (hex), NULL if none
    confidence   REAL,
    hop_count    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS uploads_image ON uploads(image_id);

CREATE TABLE IF NOT EXISTS hops (
    upload_id    INTEGER NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    position     INTEGER NOT NULL,          -- 1 = origin
    device_id    TEXT NOT NULL,             -- hex
    imei         TEXT,
    timestamp    INTEGER NOT NULL,
    role         TEXT NOT NULL,
    PRIMARY KEY (upload_id, position)
);
CREATE INDEX IF NOT EXISTS hops_device ON hops(device_id);

CREATE TABLE IF NOT EXISTS devices (
    device_id    TEXT PRIMARY KEY,          -- hex
    label        TEXT,
    notes        TEXT,
    updated_at   TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def connect() -> sqlite3.Connection:
    """A new connection per request (FastAPI runs sync endpoints in threads)."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init() -> None:
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(SCHEMA)
        # Databases created before video support lack these columns.
        cols = {r["name"] for r in con.execute("PRAGMA table_info(uploads)")}
        if "media" not in cols:
            con.execute("ALTER TABLE uploads ADD COLUMN media TEXT NOT NULL DEFAULT 'image'")
        if "ext" not in cols:
            con.execute("ALTER TABLE uploads ADD COLUMN ext TEXT NOT NULL DEFAULT '.jpg'")

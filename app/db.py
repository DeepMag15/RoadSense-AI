"""Tiny SQLite persistence layer (no ORM needed for three tables)."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager

from .config import DB_PATH

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS inspections (
    id TEXT PRIMARY KEY,
    name TEXT, road_name TEXT, location TEXT,
    lat REAL, lon REAL,
    media_type TEXT,
    status TEXT, progress REAL DEFAULT 0, message TEXT,
    created_at TEXT, finished_at TEXT,
    settings TEXT, summary TEXT,
    video_path TEXT
);
CREATE TABLE IF NOT EXISTS frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inspection_id TEXT, idx INTEGER, source_name TEXT, t_sec REAL,
    width INTEGER, height INTEGER,
    image_path TEXT, annotated_path TEXT,
    lat REAL, lon REAL
);
CREATE TABLE IF NOT EXISTS detections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inspection_id TEXT, frame_id INTEGER, track_id INTEGER,
    cls TEXT, conf REAL, score REAL, severity TEXT,
    x1 REAL, y1 REAL, x2 REAL, y2 REAL,
    polygon TEXT, metrics TEXT, recommendation TEXT, crop_path TEXT,
    t_sec REAL, hits INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_det_insp ON detections(inspection_id);
CREATE INDEX IF NOT EXISTS ix_frame_insp ON frames(inspection_id);
"""

JSON_COLS = {"settings", "summary", "polygon", "metrics"}


@contextmanager
def conn():
    with _lock:
        c = sqlite3.connect(DB_PATH)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        # jobs interrupted by a server restart can never finish
        c.execute("UPDATE inspections SET status='failed', message='Interrupted by server restart' "
                  "WHERE status IN ('queued','processing')")


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS & d.keys():
        if d[k]:
            d[k] = json.loads(d[k])
    return d


def _enc(v):
    return json.dumps(v) if isinstance(v, (dict, list)) else v


def insert(table: str, **fields) -> int:
    cols = ", ".join(fields)
    qs = ", ".join("?" for _ in fields)
    with conn() as c:
        cur = c.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", [_enc(v) for v in fields.values()])
        return cur.lastrowid


def update(table: str, key: str | int, **fields):
    sets = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE {table} SET {sets} WHERE id=?", [_enc(v) for v in fields.values()] + [key])


def query(sql: str, params=()) -> list[dict]:
    with conn() as c:
        return [_row(r) for r in c.execute(sql, params).fetchall()]


def one(sql: str, params=()) -> dict | None:
    with conn() as c:
        return _row(c.execute(sql, params).fetchone())


def clear_results(insp_id: str):
    with conn() as c:
        for t in ("detections", "frames"):
            c.execute(f"DELETE FROM {t} WHERE inspection_id=?", (insp_id,))


def delete_inspection(insp_id: str):
    with conn() as c:
        for t, col in (("detections", "inspection_id"), ("frames", "inspection_id"), ("inspections", "id")):
            c.execute(f"DELETE FROM {t} WHERE {col}=?", (insp_id,))

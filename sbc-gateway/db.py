"""SQLite persistence — the audit trail for PAR status changes and the
ICS-214 activity log. Fusion state (positions) is intentionally NOT
persisted here; it's live/derived and rebuilding it from scratch on
restart is correct behavior, not data loss.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "db" / "telemetry.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ics214_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL,
    node_timestamp_ms INTEGER NOT NULL,
    received_at_s REAL NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS par_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    node_since_ms INTEGER NOT NULL,
    received_at_s REAL NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.executescript(_SCHEMA)
    return conn


def insert_ics214(conn: sqlite3.Connection, node_id: int, node_timestamp_ms: int, text: str):
    conn.execute(
        "INSERT INTO ics214_entries (node_id, node_timestamp_ms, received_at_s, text) VALUES (?, ?, ?, ?)",
        (node_id, node_timestamp_ms, time.time(), text),
    )
    conn.commit()


def fetch_ics214(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    rows = conn.execute(
        "SELECT node_id, node_timestamp_ms, received_at_s, text FROM ics214_entries ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {"node_id": r[0], "node_timestamp_ms": r[1], "received_at_s": r[2], "text": r[3]}
        for r in rows
    ]


def insert_par_event(conn: sqlite3.Connection, node_id: int, status: str, node_since_ms: int):
    conn.execute(
        "INSERT INTO par_events (node_id, status, node_since_ms, received_at_s) VALUES (?, ?, ?, ?)",
        (node_id, status, node_since_ms, time.time()),
    )
    conn.commit()


def fetch_par_history(conn: sqlite3.Connection, node_id: int, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT status, node_since_ms, received_at_s FROM par_events WHERE node_id = ? ORDER BY id DESC LIMIT ?",
        (node_id, limit),
    ).fetchall()
    return [{"status": r[0], "node_since_ms": r[1], "received_at_s": r[2]} for r in rows]

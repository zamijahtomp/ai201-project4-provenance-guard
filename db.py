"""SQLite storage: submissions plus a structured audit log."""
import json
import sqlite3
from datetime import datetime, timezone

DB_PATH = "provenance.db"


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS content (
                content_id TEXT PRIMARY KEY,
                creator_id TEXT NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                content_id TEXT NOT NULL,
                event TEXT NOT NULL,
                data TEXT NOT NULL
            );
            """
        )


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def save_content(content_id, creator_id, text, status):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO content VALUES (?, ?, ?, ?, ?)",
            (content_id, creator_id, text, status, now_iso()),
        )


def log_event(content_id, event, data):
    """Append a structured audit entry. `data` is a JSON-serializable dict."""
    with _conn() as conn:
        conn.execute(
            "INSERT INTO audit_log (timestamp, content_id, event, data) VALUES (?, ?, ?, ?)",
            (now_iso(), content_id, event, json.dumps(data)),
        )


def get_log(limit=50):
    """Most recent audit entries, newest first, flattened to one JSON object each."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [
        {
            "timestamp": r["timestamp"],
            "content_id": r["content_id"],
            "event": r["event"],
            **json.loads(r["data"]),
        }
        for r in rows
    ]

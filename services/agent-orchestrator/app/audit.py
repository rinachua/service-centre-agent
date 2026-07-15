import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    request_id TEXT PRIMARY KEY,
    user_query TEXT NOT NULL,
    tool_calls TEXT NOT NULL,
    injection_flags TEXT NOT NULL,
    schema_validation_failures TEXT NOT NULL DEFAULT '[]',
    final_answer TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def new_request_id() -> str:
    return f"REQ-{uuid.uuid4().hex[:10]}"


def record(
    conn, request_id, user_query, tool_calls, injection_flags, final_answer,
    schema_validation_failures=None,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO audit_log
           (request_id, user_query, tool_calls, injection_flags,
            schema_validation_failures, final_answer, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            request_id, user_query, json.dumps(tool_calls), json.dumps(injection_flags),
            json.dumps(schema_validation_failures or []),
            json.dumps(final_answer), datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def get(conn, request_id: str):
    row = conn.execute("SELECT * FROM audit_log WHERE request_id = ?", (request_id,)).fetchone()
    if row is None:
        return None
    return {
        "request_id": row["request_id"],
        "user_query": row["user_query"],
        "tool_calls": json.loads(row["tool_calls"]),
        "injection_flags": json.loads(row["injection_flags"]),
        "schema_validation_failures": json.loads(row["schema_validation_failures"]),
        "final_answer": json.loads(row["final_answer"]),
        "created_at": row["created_at"],
    }


def list_recent(conn, limit: int = 20):
    """Summary rows for the audit-trail dashboard — deliberately excludes the full
    tool_calls/final_answer payloads (fetch a single entry via get() for that); a list
    view only needs enough to identify and skim each request."""
    rows = conn.execute(
        """SELECT request_id, user_query, injection_flags, schema_validation_failures,
                  final_answer, created_at
           FROM audit_log ORDER BY created_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    entries = []
    for row in rows:
        final_answer = json.loads(row["final_answer"])
        entries.append({
            "request_id": row["request_id"],
            "user_query": row["user_query"],
            "confidence": final_answer.get("confidence"),
            "had_injection_flags": bool(json.loads(row["injection_flags"])),
            "had_schema_validation_failures": bool(json.loads(row["schema_validation_failures"])),
            "created_at": row["created_at"],
        })
    return entries

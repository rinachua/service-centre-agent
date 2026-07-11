import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY,
    tool_id TEXT NOT NULL,
    line TEXT NOT NULL,
    process_area TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    downtime_impact_hours REAL NOT NULL,
    reported_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS followups (
    followup_id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id),
    summary TEXT NOT NULL,
    root_cause TEXT NOT NULL,
    next_action TEXT NOT NULL,
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


def seed_if_empty(conn: sqlite3.Connection, seed_path: Path) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM tickets").fetchone()
    if row["n"] > 0:
        return
    tickets = json.loads((seed_path / "tickets.json").read_text())
    conn.executemany(
        """INSERT INTO tickets
           (ticket_id, tool_id, line, process_area, title, description,
            severity, status, downtime_impact_hours, reported_by, created_at)
           VALUES (:ticket_id, :tool_id, :line, :process_area, :title,
                   :description, :severity, :status, :downtime_impact_hours,
                   :reported_by, :created_at)""",
        tickets,
    )
    conn.commit()

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    tool_id TEXT PRIMARY KEY,
    line TEXT NOT NULL,
    process_area TEXT NOT NULL,
    status TEXT NOT NULL,
    recent_downtime_hours_7d REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    record_id TEXT PRIMARY KEY,
    tool_id TEXT NOT NULL REFERENCES assets(tool_id),
    event_type TEXT NOT NULL,
    code TEXT NOT NULL,
    description TEXT NOT NULL,
    date TEXT NOT NULL,
    resolution TEXT NOT NULL,
    parts_replaced TEXT NOT NULL
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
    row = conn.execute("SELECT COUNT(*) AS n FROM assets").fetchone()
    if row["n"] > 0:
        return
    assets = json.loads((seed_path / "assets.json").read_text())
    conn.executemany(
        """INSERT INTO assets
           (tool_id, line, process_area, status, recent_downtime_hours_7d)
           VALUES (:tool_id, :line, :process_area, :status, :recent_downtime_hours_7d)""",
        assets,
    )
    history = json.loads((seed_path / "history.json").read_text())
    conn.executemany(
        """INSERT INTO history
           (record_id, tool_id, event_type, code, description, date, resolution, parts_replaced)
           VALUES (:record_id, :tool_id, :event_type, :code, :description, :date, :resolution, :parts_replaced)""",
        history,
    )
    conn.commit()

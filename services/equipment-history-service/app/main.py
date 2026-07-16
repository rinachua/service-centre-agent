import os
from pathlib import Path

from fastapi import FastAPI, HTTPException

from app.db import connect, seed_if_empty
from app.logging_middleware import RequestIDMiddleware, configure_logging
from app.schemas import Asset, HistoryRecord


def create_app(db_path: str, seed_path: Path) -> FastAPI:
    configure_logging("equipment-history-service")
    app = FastAPI(title="equipment-history-service")
    app.add_middleware(RequestIDMiddleware)
    conn = connect(db_path)
    seed_if_empty(conn, seed_path)

    @app.get("/health")
    def health():
        try:
            conn.execute("SELECT 1")
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc
        return {"status": "ok"}

    @app.get("/assets", response_model=list[Asset])
    def list_assets():
        rows = conn.execute("SELECT * FROM assets ORDER BY tool_id").fetchall()
        return [dict(r) for r in rows]

    @app.get("/assets/{tool_id}", response_model=Asset)
    def get_asset(tool_id: str):
        row = conn.execute(
            "SELECT * FROM assets WHERE tool_id = ?", (tool_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Asset {tool_id} not found")
        return dict(row)

    @app.get("/assets/{tool_id}/history", response_model=list[HistoryRecord])
    def get_history(tool_id: str):
        asset = conn.execute(
            "SELECT tool_id FROM assets WHERE tool_id = ?", (tool_id,)
        ).fetchone()
        if asset is None:
            raise HTTPException(status_code=404, detail=f"Asset {tool_id} not found")
        rows = conn.execute(
            "SELECT * FROM history WHERE tool_id = ? ORDER BY date DESC", (tool_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    @app.get("/history/search", response_model=list[HistoryRecord])
    def search_history(q: str):
        like = f"%{q.lower()}%"
        rows = conn.execute(
            """SELECT * FROM history
               WHERE lower(code) LIKE ? OR lower(description) LIKE ?
                  OR lower(resolution) LIKE ?
               ORDER BY date DESC""",
            (like, like, like),
        ).fetchall()
        return [dict(r) for r in rows]

    return app


app = create_app(
    db_path=os.environ.get("DB_PATH", "/app/data-local/equipment.db"),
    seed_path=Path(os.environ.get("SEED_PATH", "/app/data/seed")),
)

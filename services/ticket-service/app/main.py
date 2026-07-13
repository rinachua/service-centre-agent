import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException

from app.db import connect, seed_if_empty
from app.logging_middleware import RequestIDMiddleware, configure_logging
from app.schemas import Followup, FollowupCreate, Ticket


def create_app(db_path: str, seed_path: Path) -> FastAPI:
    configure_logging("ticket-service")
    app = FastAPI(title="ticket-service")
    app.add_middleware(RequestIDMiddleware)
    conn = connect(db_path)
    seed_if_empty(conn, seed_path)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/tickets", response_model=list[Ticket])
    def list_tickets(status: str | None = None, tool_id: str | None = None):
        query = "SELECT * FROM tickets WHERE 1=1"
        params: list[str] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if tool_id:
            query += " AND tool_id = ?"
            params.append(tool_id)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    @app.get("/tickets/{ticket_id}", response_model=Ticket)
    def get_ticket(ticket_id: str):
        row = conn.execute(
            "SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found")
        return dict(row)

    @app.post("/tickets/{ticket_id}/followups", response_model=Followup, status_code=201)
    def create_followup(ticket_id: str, body: FollowupCreate):
        ticket = conn.execute(
            "SELECT ticket_id FROM tickets WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        if ticket is None:
            raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found")
        followup_id = f"FUP-{uuid.uuid4().hex[:8]}"
        created_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO followups
               (followup_id, ticket_id, summary, root_cause, next_action, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (followup_id, ticket_id, body.summary, body.root_cause, body.next_action, created_at),
        )
        conn.commit()
        return Followup(
            followup_id=followup_id,
            ticket_id=ticket_id,
            summary=body.summary,
            root_cause=body.root_cause,
            next_action=body.next_action,
            created_at=created_at,
        )

    @app.get("/tickets/{ticket_id}/followups", response_model=list[Followup])
    def list_followups(ticket_id: str):
        rows = conn.execute(
            "SELECT * FROM followups WHERE ticket_id = ? ORDER BY created_at DESC",
            (ticket_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    return app


app = create_app(
    db_path=os.environ.get("DB_PATH", "/app/data-local/tickets.db"),
    seed_path=Path(os.environ.get("SEED_PATH", "/app/data/seed")),
)

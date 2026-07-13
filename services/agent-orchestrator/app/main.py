import os
from pathlib import Path

import anthropic
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import audit as audit_module
from app.loop import run_agent_loop
from app.logging_middleware import RequestIDMiddleware, configure_logging
from app.offline_responder import OfflineResponder
from app.tools import ToolExecutor


class ChatRequest(BaseModel):
    query: str


class FollowupCreateRequest(BaseModel):
    summary: str
    root_cause: str
    next_action: str


def create_app(
    anthropic_client,
    planner_model: str,
    synthesis_model: str,
    ticket_url: str,
    equipment_url: str,
    knowledge_url: str,
    recommendation_url: str,
    audit_db_path: str,
    static_dir: Path | None = None,
) -> FastAPI:
    configure_logging("agent-orchestrator")
    app = FastAPI(title="agent-orchestrator")
    app.add_middleware(RequestIDMiddleware)
    audit_conn = audit_module.connect(audit_db_path)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/chat")
    def chat(body: ChatRequest):
        request_id = audit_module.new_request_id()
        executor = ToolExecutor(
            ticket_url=ticket_url, equipment_url=equipment_url,
            knowledge_url=knowledge_url, recommendation_url=recommendation_url,
            request_id=request_id,
        )
        try:
            answer, trace = run_agent_loop(
                anthropic_client, planner_model, synthesis_model, body.query, executor,
            )
        except anthropic.APIError as exc:
            raise HTTPException(status_code=502, detail=f"LLM provider error: {exc}") from exc

        answer_dict = answer.model_dump()
        audit_module.record(
            audit_conn, request_id, body.query, trace.tool_calls,
            trace.injection_flags, answer_dict,
        )
        return {"request_id": request_id, "answer": answer_dict}

    @app.get("/audit/{request_id}")
    def get_audit(request_id: str):
        entry = audit_module.get(audit_conn, request_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Not found")
        return entry

    @app.post("/tickets/{ticket_id}/followups", status_code=201)
    def save_followup(ticket_id: str, body: FollowupCreateRequest):
        try:
            with httpx.Client(timeout=3.0) as client:
                resp = client.post(f"{ticket_url}/tickets/{ticket_id}/followups", json=body.model_dump())
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"ticket-service error: {exc}") from exc

    if static_dir and static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


def _build_anthropic_client() -> object:
    """Picks the real Anthropic client when ANTHROPIC_API_KEY is set, or a deterministic
    rule-based OfflineResponder otherwise, so the full demo runs with zero setup when no
    key is available. Both implement the same duck-typed `.messages.create(**kwargs)`
    interface app/loop.py expects. See spec §6.6."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return OfflineResponder()


app = create_app(
    anthropic_client=_build_anthropic_client(),
    planner_model=os.environ.get("CLAUDE_PLANNER_MODEL", "claude-haiku-4-5-20251001"),
    synthesis_model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-5"),
    ticket_url=os.environ.get("TICKET_SERVICE_URL", "http://ticket-service:8001"),
    equipment_url=os.environ.get("EQUIPMENT_SERVICE_URL", "http://equipment-history-service:8002"),
    knowledge_url=os.environ.get("KNOWLEDGE_SERVICE_URL", "http://knowledge-service:8003"),
    recommendation_url=os.environ.get("RECOMMENDATION_SERVICE_URL", "http://recommendation-service:8004"),
    audit_db_path=os.environ.get("AUDIT_DB_PATH", "/app/data-local/audit.db"),
    static_dir=Path(__file__).parent.parent / "static",
)

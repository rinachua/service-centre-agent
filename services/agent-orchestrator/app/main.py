import os
from pathlib import Path

import anthropic
import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import audit as audit_module
from app.logging_middleware import RequestIDMiddleware, configure_logging
from app.loop import run_agent_loop
from app.offline_responder import OfflineResponder
from app.tools import ServiceError, ToolExecutor

# RBAC assumptions stub (spec §9.3) — caller-asserted, unauthenticated. See
# app/loop.py's _ROLE_FRAMING for exactly what this does and does not change.
_KNOWN_ROLES = {"engineer", "manager"}
_DEFAULT_ROLE = "engineer"


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
        try:
            audit_conn.execute("SELECT 1")
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"audit database unavailable: {exc}") from exc
        return {"status": "ok"}

    @app.post("/chat")
    def chat(body: ChatRequest, x_user_role: str | None = Header(default=None)):
        request_id = audit_module.new_request_id()
        # Caller-asserted, unauthenticated: normalise anything unrecognised to the
        # default rather than erroring — this is a framing hint, not access control,
        # so an unknown/absent header is not a failure case. See _ROLE_FRAMING in
        # app/loop.py for what a role does (and does not) change.
        user_role = (x_user_role or "").strip().lower()
        if user_role not in _KNOWN_ROLES:
            user_role = _DEFAULT_ROLE
        executor = ToolExecutor(
            ticket_url=ticket_url, equipment_url=equipment_url,
            knowledge_url=knowledge_url, recommendation_url=recommendation_url,
            request_id=request_id,
        )
        try:
            answer, trace = run_agent_loop(
                anthropic_client, planner_model, synthesis_model, body.query, executor,
                user_role=user_role,
            )
        except anthropic.APIError as exc:
            raise HTTPException(status_code=502, detail=f"LLM provider error: {exc}") from exc

        answer_dict = answer.model_dump()
        audit_module.record(
            audit_conn, request_id, body.query, trace.tool_calls,
            trace.injection_flags, answer_dict,
            schema_validation_failures=trace.schema_validation_failures,
        )
        return {"request_id": request_id, "answer": answer_dict}

    @app.get("/audit/{request_id}")
    def get_audit(request_id: str):
        entry = audit_module.get(audit_conn, request_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Not found")
        return entry

    @app.get("/audit")
    def list_audit(limit: int = 20):
        return audit_module.list_recent(audit_conn, limit=limit)

    # Dashboard data endpoints (stretch goal): the browser only ever talks to
    # agent-orchestrator (same pattern as /chat and /tickets/{id}/followups above),
    # never directly to the other 4 services — those have no CORS headers configured
    # since they're only ever meant to be called server-to-server. These proxy/reuse
    # existing server-to-server calls so the dashboard can stay same-origin.
    @app.get("/dashboard/tickets")
    def dashboard_tickets(status: str | None = None):
        try:
            with httpx.Client(timeout=3.0, trust_env=False) as client:
                params = {"status": status} if status else {}
                resp = client.get(f"{ticket_url}/tickets", params=params)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"ticket-service error: {exc}") from exc

    @app.get("/dashboard/assets")
    def dashboard_assets():
        try:
            with httpx.Client(timeout=3.0, trust_env=False) as client:
                resp = client.get(f"{equipment_url}/assets")
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"equipment-history-service error: {exc}") from exc

    @app.get("/dashboard/priority")
    def dashboard_priority():
        # Reuses the same compound score_priority tool the agent itself calls
        # (app/tools.py) — fetches open tickets + their history server-side, then
        # posts to recommendation-service for ranking. Not routed through /chat: no
        # LLM call involved, this is a direct data-fetch for the dashboard view.
        executor = ToolExecutor(
            ticket_url=ticket_url, equipment_url=equipment_url,
            knowledge_url=knowledge_url, recommendation_url=recommendation_url,
            request_id=audit_module.new_request_id(),
        )
        try:
            return executor.execute("score_priority", {})
        except ServiceError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/tickets/{ticket_id}/followups", status_code=201)
    def save_followup(ticket_id: str, body: FollowupCreateRequest):
        try:
            with httpx.Client(timeout=3.0, trust_env=False) as client:
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

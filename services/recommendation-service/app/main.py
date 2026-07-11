from fastapi import FastAPI
from pydantic import BaseModel

from app.logging_middleware import RequestIDMiddleware, configure_logging
from app.scoring import rank_tickets


class ScoreRequest(BaseModel):
    tickets: list[dict]
    history: list[dict]


def create_app() -> FastAPI:
    configure_logging("recommendation-service")
    app = FastAPI(title="recommendation-service")
    app.add_middleware(RequestIDMiddleware)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/priority-score")
    def priority_score(body: ScoreRequest):
        return rank_tickets(body.tickets, body.history)

    return app


app = create_app()

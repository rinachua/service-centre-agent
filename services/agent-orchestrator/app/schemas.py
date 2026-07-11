from typing import Literal, Optional

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    source: str
    record_id: str
    detail: str
    verified: bool = True


class FollowupNote(BaseModel):
    ticket_id: str
    summary: str
    root_cause: str
    next_action: str


class AgentAnswer(BaseModel):
    recommendation: str
    evidence: list[Evidence] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"]
    next_action: str
    followup_note: Optional[FollowupNote] = None

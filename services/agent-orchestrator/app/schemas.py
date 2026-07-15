from typing import Literal

from pydantic import BaseModel, Field, model_validator


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
    followup_note: FollowupNote | None = None

    @model_validator(mode="after")
    def _high_confidence_requires_evidence(self) -> "AgentAnswer":
        """A 'high confidence' answer backed by zero evidence is a real trust smell —
        catch it structurally rather than trusting the LLM's self-reported confidence
        at face value. Downgraded, not rejected: the answer still returns, just
        honestly re-labelled, with the downgrade recorded in `assumptions` so it's
        visible to the caller rather than silently changed."""
        if self.confidence == "high" and not self.evidence:
            self.confidence = "medium"
            self.assumptions.append(
                "Confidence downgraded from 'high' to 'medium': no evidence records "
                "were cited to support it."
            )
        return self

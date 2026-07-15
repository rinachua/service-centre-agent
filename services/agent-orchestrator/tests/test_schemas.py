import pytest
from app.schemas import AgentAnswer
from pydantic import ValidationError


def test_agent_answer_rejects_invalid_confidence():
    with pytest.raises(ValidationError):
        AgentAnswer(
            recommendation="x", evidence=[], assumptions=[],
            confidence="extremely-high", next_action="y",
        )


def test_agent_answer_accepts_valid_payload_with_defaults():
    answer = AgentAnswer(recommendation="x", confidence="medium", next_action="y")
    assert answer.confidence == "medium"
    assert answer.evidence == []
    assert answer.followup_note is None


def test_agent_answer_downgrades_high_confidence_with_no_evidence():
    """A 'high confidence' answer with zero evidence is a trust smell — the model
    should catch and downgrade it, not trust the LLM's self-report at face value."""
    answer = AgentAnswer(
        recommendation="x", evidence=[], confidence="high", next_action="y",
    )
    assert answer.confidence == "medium"
    assert any("downgraded" in a for a in answer.assumptions)


def test_agent_answer_keeps_high_confidence_when_evidence_present():
    answer = AgentAnswer(
        recommendation="x",
        evidence=[{"source": "ticket-service", "record_id": "TCK-001", "detail": "d"}],
        confidence="high", next_action="y",
    )
    assert answer.confidence == "high"
    assert answer.assumptions == []


def test_agent_answer_accepts_followup_note():
    answer = AgentAnswer(
        recommendation="x", confidence="high", next_action="y",
        followup_note={
            "ticket_id": "TCK-001", "summary": "s", "root_cause": "r", "next_action": "n",
        },
    )
    assert answer.followup_note.ticket_id == "TCK-001"

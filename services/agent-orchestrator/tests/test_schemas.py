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


def test_agent_answer_accepts_followup_note():
    answer = AgentAnswer(
        recommendation="x", confidence="high", next_action="y",
        followup_note={
            "ticket_id": "TCK-001", "summary": "s", "root_cause": "r", "next_action": "n",
        },
    )
    assert answer.followup_note.ticket_id == "TCK-001"

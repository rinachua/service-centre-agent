import json

from app.loop import (
    PLAN_SYSTEM_PROMPT,
    SYNTHESIS_SYSTEM_PROMPT,
    AgentTrace,
    _build_synthesis_prompt,
    run_agent_loop,
)
from app.offline_responder import (
    OfflineResponder,
    _parse_tool_results_text,
    _plan_tools,
)
from app.tools import TOOL_DEFS


def test_plan_tools_prioritise_query_calls_score_priority():
    planned = _plan_tools("Which open equipment tickets should I prioritise today and why?")
    assert ("score_priority", {}) in planned


def test_plan_tools_tool_id_query_calls_get_equipment_history():
    planned = _plan_tools("For tool ETCH-07, summarise the recent alarm history and likely causes.")
    assert ("get_equipment_history", {"tool_id": "ETCH-07"}) in planned


def test_plan_tools_followup_query_calls_get_tickets_or_get_ticket():
    planned = _plan_tools("Generate a structured service follow-up note for the engineer.")
    names = [name for name, _ in planned]
    assert "get_tickets" in names or "get_ticket" in names


def test_plan_tools_never_returns_empty():
    planned = _plan_tools("hello")
    assert len(planned) >= 1


def test_plan_tools_only_returns_known_tool_names():
    valid_names = {t["name"] for t in TOOL_DEFS}
    for query in [
        "Which open equipment tickets should I prioritise today and why?",
        "For tool ETCH-07, summarise the recent alarm history and likely causes.",
        "Compare this issue against similar historical cases and suggest next troubleshooting steps.",
        "Generate a structured service follow-up note for the engineer.",
    ]:
        for name, _ in _plan_tools(query):
            assert name in valid_names


def test_synthesis_prompt_round_trips_through_parser():
    """Pins _build_synthesis_prompt's text format against the offline parser — if loop.py's
    prompt format ever changes, this test fails immediately instead of offline mode silently
    breaking."""
    trace = AgentTrace()
    trace.tool_calls = [
        {"tool_name": "get_tickets", "input": {"status": "open"},
         "result": [{"ticket_id": "TCK-001", "severity": "critical"}], "error": None},
        {"tool_name": "get_equipment_history", "input": {"tool_id": "ETCH-07"},
         "result": None, "error": "unreachable after retry"},
    ]
    prompt = _build_synthesis_prompt("test query", trace)
    results_text = prompt.split("\n\nTool results:\n", 1)[1]
    parsed = _parse_tool_results_text(results_text)
    assert parsed[0] == {
        "tool_name": "get_tickets",
        "result": [{"ticket_id": "TCK-001", "severity": "critical"}],
        "error": None,
    }
    assert parsed[1] == {"tool_name": "get_equipment_history", "result": None, "error": "unreachable after retry"}


def test_synthesis_prompt_round_trips_with_no_tool_results():
    trace = AgentTrace()
    prompt = _build_synthesis_prompt("test query", trace)
    results_text = prompt.split("\n\nTool results:\n", 1)[1]
    assert _parse_tool_results_text(results_text) == []


def test_offline_responder_plan_call_returns_tool_use_blocks():
    responder = OfflineResponder()
    response = responder.messages.create(
        model="offline", max_tokens=1024, system=PLAN_SYSTEM_PROMPT,
        tools=TOOL_DEFS, tool_choice={"type": "any"},
        messages=[{"role": "user", "content": "Which open equipment tickets should I prioritise today and why?"}],
    )
    assert all(b.type == "tool_use" for b in response.content)
    assert any(b.name == "score_priority" for b in response.content)


def test_offline_responder_synthesis_call_returns_valid_answer_json():
    responder = OfflineResponder()
    trace = AgentTrace()
    trace.tool_calls = [
        {"tool_name": "score_priority", "input": {},
         "result": [{"ticket_id": "TCK-002", "score": 0.91, "recurrence_count": 3}], "error": None},
    ]
    prompt = _build_synthesis_prompt("Which open equipment tickets should I prioritise today and why?", trace)
    response = responder.messages.create(
        model="offline", max_tokens=1500, system=SYNTHESIS_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in response.content if b.type == "text")
    parsed = json.loads(text)
    assert parsed["sufficient"] is True
    assert parsed["additional_tool_request"] is None
    assert parsed["answer"]["confidence"] == "low"
    assert any(e["record_id"] == "TCK-002" for e in parsed["answer"]["evidence"])
    assert "offline demo mode" in parsed["answer"]["assumptions"][0]


def test_offline_responder_runs_through_full_run_agent_loop():
    """End-to-end proof that OfflineResponder satisfies run_agent_loop's real contract —
    same test double pattern the FakeAnthropicClient tests already use, but exercising the
    actual rule-based responder instead of scripted fixtures."""

    class _StubToolExecutor:
        raw_results: list = []

        def execute(self, tool_name, tool_input):
            self.raw_results = [{"ticket_id": "TCK-002", "score": 0.91}]
            return [{"ticket_id": "TCK-002", "score": 0.91}]

    answer, trace = run_agent_loop(
        OfflineResponder(), "offline-planner", "offline-synth",
        "Which open equipment tickets should I prioritise today and why?",
        _StubToolExecutor(),
    )
    assert answer.confidence == "low"
    assert trace.revised is False
    assert len(trace.tool_calls) >= 1

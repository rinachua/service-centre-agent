import httpx
import respx
from app.tools import TOOL_DEFS, ServiceError, ToolExecutor, request_with_retry

URLS = {
    "ticket_url": "http://ticket-service:8001",
    "equipment_url": "http://equipment:8002",
    "knowledge_url": "http://knowledge:8003",
    "recommendation_url": "http://recommendation:8004",
}


def _executor():
    return ToolExecutor(request_id="REQ-test", **URLS)


@respx.mock
def test_get_tickets_calls_ticket_service():
    respx.get("http://ticket-service:8001/tickets").mock(
        return_value=httpx.Response(200, json=[{"ticket_id": "TCK-001"}])
    )
    result = _executor().execute("get_tickets", {"status": "open", "tool_id": None})
    assert result == [{"ticket_id": "TCK-001"}]


@respx.mock
def test_get_ticket_raises_service_error_on_404():
    respx.get("http://ticket-service:8001/tickets/TCK-999").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    try:
        _executor().execute("get_ticket", {"ticket_id": "TCK-999"})
        raise AssertionError("expected ServiceError")
    except ServiceError as exc:
        assert "404" in exc.detail


@respx.mock
def test_get_equipment_without_tool_id_lists_all():
    respx.get("http://equipment:8002/assets").mock(
        return_value=httpx.Response(200, json=[{"tool_id": "ETCH-07"}])
    )
    result = _executor().execute("get_equipment", {})
    assert result == [{"tool_id": "ETCH-07"}]


@respx.mock
def test_get_equipment_with_tool_id_gets_single_asset():
    respx.get("http://equipment:8002/assets/ETCH-07").mock(
        return_value=httpx.Response(200, json={"tool_id": "ETCH-07"})
    )
    result = _executor().execute("get_equipment", {"tool_id": "ETCH-07"})
    assert result == {"tool_id": "ETCH-07"}


@respx.mock
def test_search_knowledge_passes_query_and_top_k():
    respx.get("http://knowledge:8003/search").mock(
        return_value=httpx.Response(200, json=[{"doc_id": "DOC-001"}])
    )
    result = _executor().execute("search_knowledge", {"query": "rf alarm", "top_k": 3})
    assert result == [{"doc_id": "DOC-001"}]


@respx.mock
def test_score_priority_fetches_tickets_history_then_posts_to_recommendation():
    respx.get("http://ticket-service:8001/tickets").mock(
        return_value=httpx.Response(200, json=[{"ticket_id": "TCK-001", "tool_id": "ETCH-07"}])
    )
    respx.get("http://equipment:8002/assets/ETCH-07/history").mock(
        return_value=httpx.Response(200, json=[{"record_id": "HIST-001", "tool_id": "ETCH-07"}])
    )
    respx.post("http://recommendation:8004/priority-score").mock(
        return_value=httpx.Response(200, json=[{"ticket_id": "TCK-001", "score": 0.9}])
    )
    result = _executor().execute("score_priority", {})
    assert result == [{"ticket_id": "TCK-001", "score": 0.9}]


@respx.mock
def test_score_priority_filters_by_ticket_ids_when_provided():
    respx.get("http://ticket-service:8001/tickets").mock(
        return_value=httpx.Response(200, json=[
            {"ticket_id": "TCK-001", "tool_id": "ETCH-07"},
            {"ticket_id": "TCK-002", "tool_id": "CMP-02"},
        ])
    )
    respx.get("http://equipment:8002/assets/ETCH-07/history").mock(
        return_value=httpx.Response(200, json=[])
    )
    route = respx.post("http://recommendation:8004/priority-score").mock(
        return_value=httpx.Response(200, json=[{"ticket_id": "TCK-001", "score": 0.5}])
    )
    _executor().execute("score_priority", {"ticket_ids": ["TCK-001"]})
    sent_body = route.calls.last.request.content
    assert b"TCK-002" not in sent_body


def test_tool_defs_names_match_executor_dispatch():
    names = {tool["name"] for tool in TOOL_DEFS}
    assert names == {
        "get_tickets", "get_ticket", "get_equipment", "get_equipment_history",
        "search_history", "search_knowledge", "score_priority",
    }


def test_execute_unknown_tool_raises_value_error():
    try:
        _executor().execute("not_a_real_tool", {})
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


@respx.mock
def test_request_with_retry_succeeds_after_one_transient_failure():
    """Regression test: previously agent-orchestrator's dashboard/followup endpoints
    made one-shot httpx calls with no retry at all — the only downstream calls in the
    codebase without ToolExecutor's 1-retry protection. request_with_retry is the
    shared helper that closes that gap; this proves it actually retries rather than
    just refactoring the code to look the same."""
    route = respx.get("http://flaky-service/thing").mock(
        side_effect=[httpx.ConnectError("connection reset"), httpx.Response(200, json={"ok": True})]
    )
    result = request_with_retry("GET", "http://flaky-service/thing")
    assert result == {"ok": True}
    assert route.call_count == 2


@respx.mock
def test_request_with_retry_does_not_retry_on_http_status_error():
    """A 404/500 won't change on retry — only connection-level failures should retry."""
    route = respx.get("http://real-error-service/thing").mock(
        return_value=httpx.Response(500, json={"detail": "boom"})
    )
    try:
        request_with_retry("GET", "http://real-error-service/thing")
        raise AssertionError("expected HTTPStatusError")
    except httpx.HTTPStatusError:
        pass
    assert route.call_count == 1


@respx.mock
def test_request_with_retry_raises_after_exhausting_both_attempts():
    route = respx.get("http://always-down/thing").mock(side_effect=httpx.ConnectError("down"))
    try:
        request_with_retry("GET", "http://always-down/thing")
        raise AssertionError("expected httpx.HTTPError")
    except httpx.HTTPError:
        pass
    assert route.call_count == 2


def test_execute_dispatches_through_handler_registry_not_string_matching():
    """Regression/design test: execute() must route through the _handlers registry
    (one entry per tool) rather than a hardcoded if/elif chain, so adding a new tool
    is a one-line registry addition, not an edit to a growing conditional."""
    executor = _executor()
    assert set(executor._handlers) == {
        "get_tickets", "get_ticket", "get_equipment", "get_equipment_history",
        "search_history", "search_knowledge", "score_priority",
    }
    assert all(callable(h) for h in executor._handlers.values())

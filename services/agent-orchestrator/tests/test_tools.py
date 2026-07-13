import httpx
import respx
from app.tools import TOOL_DEFS, ServiceError, ToolExecutor

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

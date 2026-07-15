import httpx

TOOL_DEFS = [
    {
        "name": "get_tickets",
        "description": "List service tickets, optionally filtered by status and/or tool_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "open, in_progress, or closed"},
                "tool_id": {"type": "string", "description": "e.g. ETCH-07"},
            },
        },
    },
    {
        "name": "get_ticket",
        "description": "Get full detail for a single ticket by ID.",
        "input_schema": {
            "type": "object",
            "properties": {"ticket_id": {"type": "string"}},
            "required": ["ticket_id"],
        },
    },
    {
        "name": "get_equipment",
        "description": "Get equipment asset status. Omit tool_id to list all assets.",
        "input_schema": {
            "type": "object",
            "properties": {"tool_id": {"type": "string"}},
        },
    },
    {
        "name": "get_equipment_history",
        "description": "Get alarm and maintenance history for a specific tool_id.",
        "input_schema": {
            "type": "object",
            "properties": {"tool_id": {"type": "string"}},
            "required": ["tool_id"],
        },
    },
    {
        "name": "search_history",
        "description": "Keyword search across all alarm/maintenance history records.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "search_knowledge",
        "description": "Search troubleshooting guides, SOP excerpts, and shift notes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "description": "default 5"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "score_priority",
        "description": "Get deterministic priority scores for open tickets, ranked highest first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Omit to score all open tickets.",
                },
            },
        },
    },
]


class ServiceError(Exception):
    def __init__(self, service: str, detail: str):
        self.service = service
        self.detail = detail
        super().__init__(f"{service}: {detail}")


def request_with_retry(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    json_body: dict | None = None,
    timeout: float = 3.0,
):
    """One retry on connection-level failure only — an HTTP error status (4xx/5xx)
    won't change on retry, so those raise immediately. trust_env=False: these are
    server-to-server calls inside the Docker network and must not respect host proxy
    env vars. Shared by ToolExecutor and by agent-orchestrator's dashboard/followup
    endpoints (app/main.py), which previously made one-shot calls with no retry at
    all — the only downstream calls in the codebase without this protection."""
    last_error: httpx.HTTPError | None = None
    for _ in range(2):
        try:
            with httpx.Client(timeout=timeout, trust_env=False) as client:
                resp = client.request(method, url, params=params, json=json_body, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError:
            raise
        except httpx.HTTPError as exc:
            last_error = exc
    raise last_error


class ToolExecutor:
    def __init__(
        self,
        ticket_url: str,
        equipment_url: str,
        knowledge_url: str,
        recommendation_url: str,
        request_id: str,
        timeout: float = 3.0,
    ):
        self.ticket_url = ticket_url
        self.equipment_url = equipment_url
        self.knowledge_url = knowledge_url
        self.recommendation_url = recommendation_url
        self.headers = {"X-Request-ID": request_id}
        self.timeout = timeout
        # Every raw JSON payload fetched from a downstream service during the
        # most recent execute() call, in fetch order. A compound tool (e.g.
        # score_priority) may hit several services internally and return only
        # a synthesised result to the caller/LLM; the grounding check still
        # needs to see every real record fetched along the way, so callers of
        # execute() (see app/loop.py) read this instead of just the return
        # value when building the known-ID set for evidence verification.
        self.raw_results: list = []
        # Registry, not an if/elif chain: adding tool #8 means adding one line here
        # plus one TOOL_DEFS entry, not editing a growing conditional. Built here
        # (not as a class attribute) since each entry is a bound method.
        self._handlers = {
            "get_tickets": self._tool_get_tickets,
            "get_ticket": self._tool_get_ticket,
            "get_equipment": self._tool_get_equipment,
            "get_equipment_history": self._tool_get_equipment_history,
            "search_history": self._tool_search_history,
            "search_knowledge": self._tool_search_knowledge,
            "score_priority": self._tool_score_priority,
        }

    def _get(self, base_url: str, path: str, params: dict | None = None):
        try:
            data = request_with_retry(
                "GET", f"{base_url}{path}", headers=self.headers, params=params, timeout=self.timeout
            )
        except httpx.HTTPStatusError as exc:
            raise ServiceError(base_url, f"HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ServiceError(base_url, f"unreachable after retry: {exc}") from exc
        self.raw_results.append(data)
        return data

    def _post(self, base_url: str, path: str, json_body: dict):
        try:
            data = request_with_retry(
                "POST", f"{base_url}{path}", headers=self.headers, json_body=json_body, timeout=self.timeout
            )
        except httpx.HTTPStatusError as exc:
            raise ServiceError(base_url, f"HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ServiceError(base_url, f"unreachable after retry: {exc}") from exc
        self.raw_results.append(data)
        return data

    def _tool_get_tickets(self, tool_input: dict):
        params = {k: v for k, v in tool_input.items() if v is not None}
        return self._get(self.ticket_url, "/tickets", params)

    def _tool_get_ticket(self, tool_input: dict):
        return self._get(self.ticket_url, f"/tickets/{tool_input['ticket_id']}")

    def _tool_get_equipment(self, tool_input: dict):
        tool_id = tool_input.get("tool_id")
        if tool_id:
            return self._get(self.equipment_url, f"/assets/{tool_id}")
        return self._get(self.equipment_url, "/assets")

    def _tool_get_equipment_history(self, tool_input: dict):
        return self._get(self.equipment_url, f"/assets/{tool_input['tool_id']}/history")

    def _tool_search_history(self, tool_input: dict):
        return self._get(self.equipment_url, "/history/search", {"q": tool_input["query"]})

    def _tool_search_knowledge(self, tool_input: dict):
        params = {"q": tool_input["query"], "top_k": tool_input.get("top_k", 5)}
        return self._get(self.knowledge_url, "/search", params)

    def _tool_score_priority(self, tool_input: dict):
        ticket_ids = tool_input.get("ticket_ids")
        tickets = self._get(self.ticket_url, "/tickets", {"status": "open"})
        if ticket_ids:
            tickets = [t for t in tickets if t["ticket_id"] in ticket_ids]
        tool_ids = {t["tool_id"] for t in tickets}
        history: list[dict] = []
        for tid in tool_ids:
            try:
                history.extend(self._get(self.equipment_url, f"/assets/{tid}/history"))
            except ServiceError:
                continue
        return self._post(
            self.recommendation_url,
            "/priority-score",
            {"tickets": tickets, "history": history},
        )

    def execute(self, tool_name: str, tool_input: dict):
        self.raw_results = []
        handler = self._handlers.get(tool_name)
        if handler is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        return handler(tool_input)

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

    def _get(self, base_url: str, path: str, params: dict | None = None):
        last_error: Exception | None = None
        for _ in range(2):
            try:
                with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                    resp = client.get(f"{base_url}{path}", params=params, headers=self.headers)
                    resp.raise_for_status()
                    data = resp.json()
                    self.raw_results.append(data)
                    return data
            except httpx.HTTPStatusError as exc:
                raise ServiceError(base_url, f"HTTP {exc.response.status_code}") from exc
            except httpx.HTTPError as exc:
                last_error = exc
        raise ServiceError(base_url, f"unreachable after retry: {last_error}")

    def _post(self, base_url: str, path: str, json_body: dict):
        last_error: Exception | None = None
        for _ in range(2):
            try:
                with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                    resp = client.post(f"{base_url}{path}", json=json_body, headers=self.headers)
                    resp.raise_for_status()
                    data = resp.json()
                    self.raw_results.append(data)
                    return data
            except httpx.HTTPStatusError as exc:
                raise ServiceError(base_url, f"HTTP {exc.response.status_code}") from exc
            except httpx.HTTPError as exc:
                last_error = exc
        raise ServiceError(base_url, f"unreachable after retry: {last_error}")

    def execute(self, tool_name: str, tool_input: dict):
        self.raw_results = []
        if tool_name == "get_tickets":
            params = {k: v for k, v in tool_input.items() if v is not None}
            return self._get(self.ticket_url, "/tickets", params)
        if tool_name == "get_ticket":
            return self._get(self.ticket_url, f"/tickets/{tool_input['ticket_id']}")
        if tool_name == "get_equipment":
            tool_id = tool_input.get("tool_id")
            if tool_id:
                return self._get(self.equipment_url, f"/assets/{tool_id}")
            return self._get(self.equipment_url, "/assets")
        if tool_name == "get_equipment_history":
            return self._get(self.equipment_url, f"/assets/{tool_input['tool_id']}/history")
        if tool_name == "search_history":
            return self._get(self.equipment_url, "/history/search", {"q": tool_input["query"]})
        if tool_name == "search_knowledge":
            params = {"q": tool_input["query"], "top_k": tool_input.get("top_k", 5)}
            return self._get(self.knowledge_url, "/search", params)
        if tool_name == "score_priority":
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
        raise ValueError(f"Unknown tool: {tool_name}")

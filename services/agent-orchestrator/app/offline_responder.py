import json
import re

from app.loop import REVISION_SYSTEM_PROMPT

_TOOL_ID_RE = re.compile(r"\b[A-Z]{2,}-\d+\b")
_TICKET_ID_RE = re.compile(r"\bTCK-\d+\b")

_PRIORITIZE_WORDS = ("priorit", "rank")
_HISTORY_WORDS = ("history", "alarm", "recur", "similar", "compare", "cause", "historical")
_KNOWLEDGE_WORDS = ("troubleshoot", "sop", " guide", "shift note", "procedure")
_FOLLOWUP_WORDS = ("follow-up", "follow up", "generate a", "structured service")

_ID_FIELDS = ("ticket_id", "record_id", "doc_id", "tool_id")
_SOURCE_LABELS = {
    "get_tickets": "ticket-service",
    "get_ticket": "ticket-service",
    "get_equipment": "equipment-history-service",
    "get_equipment_history": "equipment-history-service",
    "search_history": "equipment-history-service",
    "search_knowledge": "knowledge-service",
    "score_priority": "recommendation-service",
}

_RESULT_LINE_RE = re.compile(r"^Tool (\w+)\(.*?\) (returned|FAILED): (.*)$")


class _TextBlock:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _ToolUseBlock:
    type = "tool_use"

    def __init__(self, name: str, input: dict, id: str):
        self.name = name
        self.input = input
        self.id = id


class _Response:
    def __init__(self, content: list):
        self.content = content


def _plan_tools(query: str) -> list[tuple[str, dict]]:
    """Rule-based stand-in for Claude's planning call. Openly heuristic, not real
    reasoning — see spec §6.6."""
    lower = query.lower()
    planned: list[tuple[str, dict]] = []

    tool_id_match = _TOOL_ID_RE.search(query)
    ticket_id_match = _TICKET_ID_RE.search(query)

    if any(w in lower for w in _PRIORITIZE_WORDS):
        planned.append(("score_priority", {}))

    if tool_id_match:
        planned.append(("get_equipment_history", {"tool_id": tool_id_match.group(0)}))
    elif any(w in lower for w in _HISTORY_WORDS):
        planned.append(("search_history", {"query": query}))

    if any(w in lower for w in _KNOWLEDGE_WORDS):
        planned.append(("search_knowledge", {"query": query, "top_k": 5}))

    if any(w in lower for w in _FOLLOWUP_WORDS):
        if ticket_id_match:
            planned.append(("get_ticket", {"ticket_id": ticket_id_match.group(0)}))
        else:
            planned.append(("get_tickets", {"status": "open"}))

    if not planned:
        planned.append(("get_tickets", {"status": "open"}))

    seen = set()
    deduped = []
    for name, input_ in planned:
        key = (name, tuple(sorted(input_.items())))
        if key not in seen:
            seen.add(key)
            deduped.append((name, input_))
    return deduped


def _parse_tool_results_text(results_text: str) -> list[dict]:
    """Recovers a tool_calls-shaped list from _build_synthesis_prompt's rendered text
    (app/loop.py). See spec §6.6 for why this parses text rather than receiving trace
    directly: OfflineResponder must satisfy the exact same call signature the real
    Anthropic SDK exposes, so it only ever sees the same prompt text a real Claude call
    would."""
    if results_text.strip() == "(no tool results)":
        return []
    parsed = []
    for line in results_text.splitlines():
        m = _RESULT_LINE_RE.match(line)
        if not m:
            continue
        tool_name, status, payload = m.groups()
        if status == "FAILED":
            parsed.append({"tool_name": tool_name, "result": None, "error": payload})
        else:
            try:
                result = json.loads(payload)
            except json.JSONDecodeError:
                result = None
            parsed.append({"tool_name": tool_name, "result": result, "error": None})
    return parsed


def _extract_evidence(tool_calls: list[dict]) -> list[dict]:
    evidence = []
    for call in tool_calls:
        if call["error"] or call["result"] is None:
            continue
        items = call["result"] if isinstance(call["result"], list) else [call["result"]]
        source = _SOURCE_LABELS.get(call["tool_name"], call["tool_name"])
        for item in items[:3]:
            if not isinstance(item, dict):
                continue
            record_id = next((item[f] for f in _ID_FIELDS if f in item), None)
            if not record_id:
                continue
            detail_bits = {k: v for k, v in item.items() if k not in _ID_FIELDS}
            detail = ", ".join(f"{k}={v}" for k, v in list(detail_bits.items())[:3])
            evidence.append({"source": source, "record_id": record_id, "detail": detail or "see raw result"})
    return evidence


def _build_answer(user_query: str, tool_calls: list[dict]) -> dict:
    evidence = _extract_evidence(tool_calls)
    errors = [c["tool_name"] for c in tool_calls if c["error"]]
    lower = user_query.lower()
    tool_names_called = {c["tool_name"] for c in tool_calls}

    if "score_priority" in tool_names_called:
        recommendation = "Based on deterministic priority scoring, review the top-ranked ticket(s) listed in evidence first."
        next_action = "Confirm the top-ranked ticket with the assigned engineer and dispatch accordingly."
    elif "get_equipment_history" in tool_names_called:
        recommendation = "Recent alarm/maintenance history for the requested tool is listed in evidence; look for recurring codes as the likely cause."
        next_action = "Have an engineer review the alarm history for recurring patterns before further troubleshooting."
    elif "search_knowledge" in tool_names_called or "search_history" in tool_names_called:
        recommendation = "Related historical cases and/or knowledge-base excerpts are listed in evidence for comparison."
        next_action = "Cross-check the current issue against the retrieved cases/guides before proceeding."
    else:
        recommendation = "Open tickets are listed in evidence; no further ranking or history lookup was requested."
        next_action = "Review the listed tickets and escalate as appropriate."

    assumptions = [
        "Generated by offline demo mode (ANTHROPIC_API_KEY not set): tool selection is "
        "rule-based and this recommendation is templated from real tool results, not live "
        "Claude reasoning.",
    ]
    if errors:
        assumptions.append(f"These tool calls failed and were skipped: {', '.join(errors)}.")
    if not evidence:
        assumptions.append("No evidence records were available from the tool calls made.")

    answer = {
        "recommendation": recommendation,
        "evidence": evidence,
        "assumptions": assumptions,
        "confidence": "low",
        "next_action": next_action,
        "followup_note": None,
    }

    if any(w in lower for w in _FOLLOWUP_WORDS):
        ticket_evidence = next((e for e in evidence if e["source"] == "ticket-service"), None)
        if ticket_evidence:
            answer["followup_note"] = {
                "ticket_id": ticket_evidence["record_id"],
                "summary": f"Draft follow-up for {ticket_evidence['record_id']} generated in offline demo mode.",
                "root_cause": "Not determined by offline demo mode; requires engineer review.",
                "next_action": next_action,
            }
            answer["assumptions"].append(
                "Follow-up note is a demo-mode placeholder; an engineer must confirm the root cause before saving."
            )

    return answer


def _extract_user_query(prompt: str) -> str:
    marker = "User question: "
    if prompt.startswith(marker):
        return prompt[len(marker):].split("\n\nTool results:", 1)[0]
    return prompt


def _extract_results_text(prompt: str) -> str:
    marker = "\n\nTool results:\n"
    idx = prompt.find(marker)
    return prompt[idx + len(marker):] if idx != -1 else ""


class OfflineResponder:
    """Deterministic, rule-based stand-in for the Anthropic client, used only when
    ANTHROPIC_API_KEY is not set (see app/main.py's bootstrap). Implements the same
    duck-typed `.messages.create(**kwargs)` interface app/loop.py expects, so the entire
    plan->execute->synthesise flow (including grounding, injection scanning, and audit
    logging) runs completely unmodified — only the source of the two/three LLM-shaped
    answers changes. See spec §6.6."""

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        system = kwargs.get("system", "")

        if "tools" in kwargs:
            query = kwargs["messages"][0]["content"]
            planned = _plan_tools(query)
            blocks = [
                _ToolUseBlock(name=name, input=input_, id=f"offline_{i}")
                for i, (name, input_) in enumerate(planned)
            ]
            return _Response(content=blocks)

        prompt = kwargs["messages"][0]["content"]
        user_query = _extract_user_query(prompt)
        tool_calls = _parse_tool_results_text(_extract_results_text(prompt))
        answer = _build_answer(user_query, tool_calls)

        if system == REVISION_SYSTEM_PROMPT:
            # Not normally reached: the synthesis branch below always reports
            # sufficient=True, so app/loop.py never triggers a revision round in
            # offline mode. Handled anyway for robustness — same answer, unwrapped
            # per the revision schema.
            return _Response(content=[_TextBlock(text=json.dumps(answer))])

        payload = {"answer": answer, "sufficient": True, "additional_tool_request": None}
        return _Response(content=[_TextBlock(text=json.dumps(payload))])

import re

INJECTION_PATTERNS = [
    r"ignore (all )?previous instructions",
    r"disregard (the )?(system|above) prompt",
    r"you are now",
    r"new instructions:",
]

ID_FIELDS = {"ticket_id", "record_id", "tool_id", "doc_id", "followup_id"}


def extract_known_ids(tool_results: list) -> set[str]:
    known_ids: set[str] = set()

    def walk(value):
        if isinstance(value, dict):
            for key, val in value.items():
                if key in ID_FIELDS and isinstance(val, str):
                    known_ids.add(val)
                walk(val)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for result in tool_results:
        walk(result)
    return known_ids


def extract_known_ticket_ids(tool_results: list) -> set[str]:
    """Same walk as extract_known_ids, but collects only values seen under the
    literal `ticket_id` key, not the broader ID_FIELDS pool (record_id, tool_id,
    doc_id, followup_id). Evidence verification deliberately treats any of those as
    an acceptable citation, but followup_note.ticket_id is used to build a real
    `/tickets/{ticket_id}/followups` URL and checked against the tickets table — a
    history record_id or a tool_id is not interchangeable with a ticket_id there,
    even though both are "known, real IDs from this session" in the broader sense."""
    known_ticket_ids: set[str] = set()

    def walk(value):
        if isinstance(value, dict):
            for key, val in value.items():
                if key == "ticket_id" and isinstance(val, str):
                    known_ticket_ids.add(val)
                walk(val)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for result in tool_results:
        walk(result)
    return known_ticket_ids


def verify_evidence(evidence: list, known_ids: set[str]) -> list:
    for item in evidence:
        item.verified = item.record_id in known_ids
    return evidence


def scan_for_injection(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in INJECTION_PATTERNS)

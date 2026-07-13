from datetime import datetime, timezone

SEVERITY_WEIGHTS = {"critical": 1.0, "high": 0.75, "medium": 0.5, "low": 0.25}


def normalize(value: float, max_value: float) -> float:
    if max_value <= 0:
        return 0.0
    return min(value / max_value, 1.0)


def age_days(created_at: str, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    created = datetime.fromisoformat(created_at)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return max((now - created).total_seconds() / 86400, 0.0)


def recurrence_count(ticket: dict, history: list[dict]) -> int:
    tool_history = [h for h in history if h["tool_id"] == ticket["tool_id"]]
    keywords = set(ticket["description"].lower().split())
    count = 0
    for h in tool_history:
        h_words = set((h["code"] + " " + h["description"]).lower().split())
        if keywords & h_words:
            count += 1
    return count


def score_ticket(ticket: dict, history: list[dict], max_downtime: float, max_age: float) -> dict:
    severity_score = SEVERITY_WEIGHTS.get(ticket["severity"], 0.25)
    downtime_score = normalize(ticket["downtime_impact_hours"], max_downtime)
    recurrence = recurrence_count(ticket, history)
    recurrence_score = normalize(recurrence, 5)
    age_score = normalize(age_days(ticket["created_at"]), max_age)

    total = (
        0.4 * severity_score
        + 0.3 * downtime_score
        + 0.2 * recurrence_score
        + 0.1 * age_score
    )
    return {
        "ticket_id": ticket["ticket_id"],
        "score": round(total, 4),
        "breakdown": {
            "severity": round(severity_score, 4),
            "downtime": round(downtime_score, 4),
            "recurrence": round(recurrence_score, 4),
            "age": round(age_score, 4),
        },
        "recurrence_count": recurrence,
    }


def rank_tickets(tickets: list[dict], history: list[dict]) -> list[dict]:
    if not tickets:
        return []
    max_downtime = max((t["downtime_impact_hours"] for t in tickets), default=1) or 1
    max_age = max((age_days(t["created_at"]) for t in tickets), default=1) or 1
    scored = [score_ticket(t, history, max_downtime, max_age) for t in tickets]
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored

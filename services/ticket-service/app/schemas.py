from pydantic import BaseModel


class Ticket(BaseModel):
    ticket_id: str
    tool_id: str
    line: str
    process_area: str
    title: str
    description: str
    severity: str
    status: str
    downtime_impact_hours: float
    reported_by: str
    created_at: str


class FollowupCreate(BaseModel):
    summary: str
    root_cause: str
    next_action: str


class Followup(BaseModel):
    followup_id: str
    ticket_id: str
    summary: str
    root_cause: str
    next_action: str
    created_at: str

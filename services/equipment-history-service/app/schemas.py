from pydantic import BaseModel


class Asset(BaseModel):
    tool_id: str
    line: str
    process_area: str
    status: str
    recent_downtime_hours_7d: float


class HistoryRecord(BaseModel):
    record_id: str
    tool_id: str
    event_type: str
    code: str
    description: str
    date: str
    resolution: str
    parts_replaced: str

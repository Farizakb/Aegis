"""Cross-week contract: RawEvent (producer -> stream) and IncidentEvent (consumer -> agent)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class FaultKind(str, Enum):
    memory_leak = "memory_leak"
    db_deadlock = "db_deadlock"
    error_spike = "error_spike"


class Severity(str, Enum):
    info = "info"
    warning = "warning"
    error = "error"
    critical = "critical"


_SEVERITY_RANK = {
    Severity.info: 0,
    Severity.warning: 1,
    Severity.error: 2,
    Severity.critical: 3,
}


def max_severity(a: Severity, b: Severity) -> Severity:
    return a if _SEVERITY_RANK[a] >= _SEVERITY_RANK[b] else b


class RawEvent(BaseModel):
    event_id: str
    fault_kind: FaultKind
    severity: Severity
    source: str
    message: str
    metric: float | None = None
    ts: datetime
    dedup_key: str


class IncidentEvent(BaseModel):
    incident_id: str
    fault_kind: FaultKind
    severity: Severity
    source: str
    title: str
    summary: str
    first_seen: datetime
    last_seen: datetime
    event_count: int
    duplicate_count: int
    sample_events: list[RawEvent]
    correlation_window_s: float


def to_stream_fields(event: RawEvent) -> dict[str, str]:
    """Flatten a RawEvent into the str/str field map XADD requires."""
    data = event.model_dump(mode="json")
    return {k: ("" if v is None else str(v)) for k, v in data.items()}


def from_stream_fields(fields: dict[str, str]) -> RawEvent:
    """Inverse of to_stream_fields."""
    data: dict = dict(fields)
    if data.get("metric", "") == "":
        data["metric"] = None
    else:
        data["metric"] = float(data["metric"])
    return RawEvent.model_validate(data)

"""Durable-fix follow-up registry + promotion queue (ADR-0003/0005)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from agent.actions import ProposedAction
from agent.state import TriageResult
from stream.schema import IncidentEvent


class DurableFixRegistry:
    """Open durable fixes filed by the report node; promotable to their own run."""

    def __init__(self) -> None:
        self._open: dict[str, dict] = {}
        self._promoted: set[str] = set()
        self._promotions: asyncio.Queue[dict] = asyncio.Queue()

    def file(self, *, incident: IncidentEvent, triage: TriageResult | None,
             fix: ProposedAction) -> None:
        iid = incident.incident_id
        if iid in self._open or iid in self._promoted:
            return
        self._open[iid] = {
            "incident": incident, "triage": triage, "fix": fix,
            "filed_at": datetime.now(timezone.utc).isoformat(),
        }

    def list_open(self) -> list[dict]:
        return [
            {"incident_id": iid, "title": e["incident"].title,
             "fault_kind": e["incident"].fault_kind.value,
             "action": e["fix"].action.value, "description": e["fix"].reason,
             "target_file": e["fix"].target_file, "filed_at": e["filed_at"]}
            for iid, e in self._open.items()
        ]

    def promote(self, incident_id: str) -> bool:
        entry = self._open.pop(incident_id, None)
        if entry is None:
            return False
        self._promoted.add(incident_id)
        self._promotions.put_nowait(entry)
        return True

    async def next_promotion(self) -> dict:
        return await self._promotions.get()

    def has_promotions(self) -> bool:
        return not self._promotions.empty()

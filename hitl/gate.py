"""Async approval gate: suspends the graph until a human resolves."""

from __future__ import annotations

import asyncio

from agent.state import HitlChoice, HitlDecision


class ApprovalGate:
    def __init__(self, notifier=None):
        self._pending: dict[str, asyncio.Future] = {}
        self._items: dict[str, dict] = {}
        self._notifier = notifier

    async def request_approval(self, *, incident, fix, verdict) -> HitlDecision:
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[HitlDecision] = loop.create_future()
        iid = incident.incident_id
        self._pending[iid] = fut
        self._items[iid] = {
            "incident_id": iid,
            "title": incident.title,
            "target_file": fix.target_file,
            "description": fix.description,
            "patch": fix.patch,
            "policy": verdict.model_dump(),
        }
        if self._notifier:
            self._notifier.notify(self._items[iid])
        return await fut

    def list_pending(self) -> list[dict]:
        return list(self._items.values())

    def resolve(self, incident_id: str, choice: HitlChoice, note: str | None = None) -> None:
        fut = self._pending.pop(incident_id, None)
        self._items.pop(incident_id, None)
        if fut and not fut.done():
            fut.set_result(HitlDecision(choice=choice, note=note))

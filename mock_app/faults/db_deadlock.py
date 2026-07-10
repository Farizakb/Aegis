"""Simulated DB deadlock: emits growing lock-wait signals on alternate ticks."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from mock_app.controls import app_version
from mock_app.faults.base import SOURCE, Fault, make_dedup_key
from stream.producer import Producer
from stream.schema import FaultKind, RawEvent, Severity

BASE_LOCK_WAIT_MS = 500
LOCK_WAIT_STEP_MS = 100
BUCKET_SIZE_MS = 500


class DbDeadlockFault(Fault):
    kind = FaultKind.db_deadlock

    def __init__(self) -> None:
        self._active = False
        self._tick = 0

    async def trigger(self) -> None:
        if app_version() == "previous":
            return  # the deadlock-causing query shipped in the last deploy
        self._active = True
        self._tick = 0

    async def clear(self) -> None:
        self._active = False
        self._tick = 0

    def is_active(self) -> bool:
        return self._active

    def metric_snapshot(self) -> dict[str, float]:
        if not self._active:
            return {}
        return {"lock_wait_ms": float(BASE_LOCK_WAIT_MS + self._tick * LOCK_WAIT_STEP_MS)}

    async def emit_signals(self, producer: Producer) -> None:
        if not self._active:
            return

        self._tick += 1
        if self._tick % 2 != 0:
            return

        lock_wait_ms = BASE_LOCK_WAIT_MS + (self._tick * LOCK_WAIT_STEP_MS)
        bucketed = (lock_wait_ms // BUCKET_SIZE_MS) * BUCKET_SIZE_MS

        event = RawEvent(
            event_id=str(uuid4()),
            fault_kind=self.kind,
            severity=Severity.error,
            source=SOURCE,
            message=f"deadlock detected on txn {self._tick}",
            metric=float(lock_wait_ms),
            ts=datetime.now(timezone.utc),
            dedup_key=make_dedup_key(self.kind, SOURCE, bucketed),
        )
        await producer.publish(event)

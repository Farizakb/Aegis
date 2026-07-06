"""Simulated memory leak: each tick grows an in-memory buffer and reports RSS."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from mock_app.faults.base import SOURCE, Fault, make_dedup_key
from stream.producer import Producer
from stream.schema import FaultKind, RawEvent, Severity

CHUNK_SIZE_MB = 5
CRITICAL_THRESHOLD_MB = 150
BUCKET_SIZE_MB = 50


class MemoryLeakFault(Fault):
    kind = FaultKind.memory_leak

    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._active = False

    async def trigger(self) -> None:
        self._active = True

    async def clear(self) -> None:
        self._active = False
        self._chunks.clear()

    def is_active(self) -> bool:
        return self._active

    def metric_snapshot(self) -> dict[str, float]:
        if not self._active:
            return {}
        return {"rss_mb": float(len(self._chunks) * CHUNK_SIZE_MB)}

    async def emit_signals(self, producer: Producer) -> None:
        if not self._active:
            return

        self._chunks.append(bytes(CHUNK_SIZE_MB * 1024 * 1024))
        rss_mb = len(self._chunks) * CHUNK_SIZE_MB
        severity = Severity.critical if rss_mb >= CRITICAL_THRESHOLD_MB else Severity.warning
        bucketed = (rss_mb // BUCKET_SIZE_MB) * BUCKET_SIZE_MB

        event = RawEvent(
            event_id=str(uuid4()),
            fault_kind=self.kind,
            severity=severity,
            source=SOURCE,
            message=f"rss high: {rss_mb}MB",
            metric=float(rss_mb),
            ts=datetime.now(timezone.utc),
            dedup_key=make_dedup_key(self.kind, SOURCE, bucketed),
        )
        await producer.publish(event)

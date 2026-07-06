"""Traffic surge: load-driven latency degradation. Restart cannot fix it — only scale_out can."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from mock_app.controls import SCALE
from mock_app.faults.base import SOURCE, Fault, make_dedup_key
from stream.producer import Producer
from stream.schema import FaultKind, RawEvent, Severity

BASE_LATENCY_MS = 50.0
LOAD_UNITS = 8              # synthetic concurrent load while the surge is active
DEGRADED_THRESHOLD_MS = 150.0
BUCKET_SIZE_MS = 50


class TrafficSurgeFault(Fault):
    kind = FaultKind.traffic_surge

    def __init__(self) -> None:
        self._active = False

    async def trigger(self) -> None:
        self._active = True

    async def clear(self) -> None:
        self._active = False

    def is_active(self) -> bool:
        return self._active

    @property
    def latency_ms(self) -> float:
        """Deterministic capacity model: latency grows with load per worker."""
        if not self._active:
            return BASE_LATENCY_MS
        return BASE_LATENCY_MS * LOAD_UNITS / SCALE.workers

    def metric_snapshot(self) -> dict[str, float]:
        if not self._active:
            return {}
        return {"latency_ms": self.latency_ms}

    async def emit_signals(self, producer: Producer) -> None:
        if not self._active:
            return

        latency = self.latency_ms
        severity = Severity.error if latency > DEGRADED_THRESHOLD_MS else Severity.warning
        bucketed = (int(latency) // BUCKET_SIZE_MS) * BUCKET_SIZE_MS

        event = RawEvent(
            event_id=str(uuid4()),
            fault_kind=self.kind,
            severity=severity,
            source=SOURCE,
            message=f"p95 latency {latency:.0f}ms under load",
            metric=latency,
            ts=datetime.now(timezone.utc),
            dedup_key=make_dedup_key(self.kind, SOURCE, bucketed),
        )
        await producer.publish(event)

"""Error spike: flag-tied — GET /work returns real 5xx while the risky feature flag is on."""

from __future__ import annotations

import random
from datetime import datetime, timezone
from uuid import uuid4

from mock_app.controls import FLAGS, RISKY_FLAG
from mock_app.faults.base import SOURCE, Fault, make_dedup_key
from stream.producer import Producer
from stream.schema import FaultKind, RawEvent, Severity

ERROR_RATE = 0.5
BUCKET_SIZE_PCT = 10


class ErrorSpikeFault(Fault):
    kind = FaultKind.error_spike

    def __init__(self) -> None:
        self._active = False
        self._total = 0
        self._errors = 0

    async def trigger(self) -> None:
        self._active = True
        self._total = 0
        self._errors = 0
        FLAGS.set(RISKY_FLAG, True)

    async def clear(self) -> None:
        self._active = False
        self._total = 0
        self._errors = 0
        FLAGS.set(RISKY_FLAG, False)

    def is_active(self) -> bool:
        return self._active

    def should_fail(self) -> bool:
        """Called by GET /work; 500s only while the risky feature flag is on."""
        if not self._active:
            return False
        self._total += 1
        if not FLAGS.is_enabled(RISKY_FLAG):
            return False
        fail = random.random() < ERROR_RATE
        if fail:
            self._errors += 1
        return fail

    async def emit_signals(self, producer: Producer) -> None:
        if not self._active or self._total == 0:
            return

        error_rate_pct = (self._errors / self._total) * 100
        bucketed = (int(error_rate_pct) // BUCKET_SIZE_PCT) * BUCKET_SIZE_PCT

        event = RawEvent(
            event_id=str(uuid4()),
            fault_kind=self.kind,
            severity=Severity.error,
            source=SOURCE,
            message=f"5xx rate {error_rate_pct:.0f}%",
            metric=error_rate_pct,
            ts=datetime.now(timezone.utc),
            dedup_key=make_dedup_key(self.kind, SOURCE, bucketed),
        )
        await producer.publish(event)

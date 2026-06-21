"""Consumes aegis:events, deduplicates and correlates them into IncidentEvents."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from stream.producer import EVENTS_STREAM
from stream.schema import FaultKind, IncidentEvent, RawEvent, Severity, from_stream_fields, max_severity

logger = logging.getLogger("aegis.consumer")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
INCIDENTS_STREAM = "aegis:incidents"
GROUP = "aegis:agent"
CONSUMER_NAME = os.environ.get("CONSUMER_NAME", "c1")
BATCH = int(os.environ.get("CONSUMER_BATCH", "10"))
BLOCK_MS = int(os.environ.get("CONSUMER_BLOCK_MS", "500"))
PENDING_HIGH_WATER = int(os.environ.get("PENDING_HIGH_WATER", "100"))
DEDUP_TTL_S = float(os.environ.get("DEDUP_TTL_S", "5"))
IDLE_GAP_S = float(os.environ.get("IDLE_GAP_S", "3"))
MAX_WINDOW_S = float(os.environ.get("MAX_WINDOW_S", "30"))
SAMPLE_CAP = 5


@dataclass
class _Window:
    incident_id: str
    fault_kind: FaultKind
    source: str
    first_seen: datetime
    last_seen: datetime
    max_severity: Severity
    opened_at: datetime
    event_count: int = 0
    duplicate_count: int = 0
    sample_events: list[RawEvent] = field(default_factory=list)


class Correlator:
    """Deduplicates raw events and groups them into IncidentEvents over a correlation window.

    Pure in-memory logic, decoupled from redis I/O, with an injectable clock for tests.
    """

    def __init__(
        self,
        dedup_ttl_s: float = DEDUP_TTL_S,
        idle_gap_s: float = IDLE_GAP_S,
        max_window_s: float = MAX_WINDOW_S,
        sample_cap: int = SAMPLE_CAP,
        now_fn=lambda: datetime.now(timezone.utc),
    ) -> None:
        self._dedup_ttl_s = dedup_ttl_s
        self._idle_gap_s = idle_gap_s
        self._max_window_s = max_window_s
        self._sample_cap = sample_cap
        self._now = now_fn
        self._dedup_seen: dict[str, datetime] = {}
        self._windows: dict[tuple[FaultKind, str], _Window] = {}

    def ingest(self, event: RawEvent) -> None:
        now = self._now()

        last_seen = self._dedup_seen.get(event.dedup_key)
        is_duplicate = last_seen is not None and (now - last_seen).total_seconds() <= self._dedup_ttl_s
        self._dedup_seen[event.dedup_key] = now

        key = (event.fault_kind, event.source)
        window = self._windows.get(key)
        if window is None:
            window = _Window(
                incident_id=str(uuid4()),
                fault_kind=event.fault_kind,
                source=event.source,
                first_seen=event.ts,
                last_seen=event.ts,
                max_severity=event.severity,
                opened_at=now,
            )
            self._windows[key] = window

        if is_duplicate:
            window.duplicate_count += 1
            window.last_seen = event.ts
            return

        window.event_count += 1
        window.last_seen = event.ts
        window.max_severity = max_severity(window.max_severity, event.severity)
        if len(window.sample_events) < self._sample_cap:
            window.sample_events.append(event)

    def sweep(self) -> list[IncidentEvent]:
        """Close windows that have gone idle or exceeded the max window; return their incidents."""
        now = self._now()
        closed: list[IncidentEvent] = []
        for key, window in list(self._windows.items()):
            idle_elapsed = (now - window.last_seen).total_seconds()
            age = (now - window.opened_at).total_seconds()
            if idle_elapsed > self._idle_gap_s or age > self._max_window_s:
                closed.append(self._to_incident(window))
                del self._windows[key]
        return closed

    def _to_incident(self, window: _Window) -> IncidentEvent:
        return IncidentEvent(
            incident_id=window.incident_id,
            fault_kind=window.fault_kind,
            severity=window.max_severity,
            source=window.source,
            title=f"{window.fault_kind.value} on {window.source}",
            summary=(
                f"{window.event_count} event(s), {window.duplicate_count} duplicate(s) "
                f"between {window.first_seen.isoformat()} and {window.last_seen.isoformat()}"
            ),
            first_seen=window.first_seen,
            last_seen=window.last_seen,
            event_count=window.event_count,
            duplicate_count=window.duplicate_count,
            sample_events=window.sample_events,
            correlation_window_s=(window.last_seen - window.first_seen).total_seconds(),
        )


class Consumer:
    """XREADGROUP loop with bounded in-flight processing (backpressure) feeding a Correlator."""

    def __init__(
        self,
        redis: Redis,
        correlator: Correlator,
        events_stream: str = EVENTS_STREAM,
        incidents_stream: str = INCIDENTS_STREAM,
        group: str = GROUP,
        consumer_name: str = CONSUMER_NAME,
        batch: int = BATCH,
        block_ms: int = BLOCK_MS,
        pending_high_water: int = PENDING_HIGH_WATER,
    ) -> None:
        self._redis = redis
        self._correlator = correlator
        self._events_stream = events_stream
        self._incidents_stream = incidents_stream
        self._group = group
        self._consumer_name = consumer_name
        self._batch = batch
        self._block_ms = block_ms
        self._pending_high_water = pending_high_water

    async def ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(self._events_stream, self._group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def recover_pending(self) -> None:
        """One-time read of this consumer's previously-pending entries on startup."""
        while True:
            messages = await self._read({self._events_stream: "0"})
            if not messages:
                break
            await self._process(messages)

    async def run_once(self) -> list[IncidentEvent]:
        """Process one poll cycle: drain backlog if high, read new entries, sweep correlator."""
        await self._drain_pending_if_high()

        messages = await self._read({self._events_stream: ">"}, block_ms=self._block_ms)
        if messages:
            await self._process(messages)

        incidents = self._correlator.sweep()
        await self._emit_incidents(incidents)
        return incidents

    async def run_forever(self) -> None:
        await self.ensure_group()
        await self.recover_pending()
        while True:
            await self.run_once()

    async def _drain_pending_if_high(self) -> None:
        summary = await self._redis.xpending(self._events_stream, self._group)
        pending_count = summary["pending"] if summary else 0
        if pending_count > self._pending_high_water:
            messages = await self._read({self._events_stream: "0"})
            if messages:
                await self._process(messages)

    async def _read(self, streams: dict[str, str], block_ms: int | None = None) -> list[tuple[str, dict]]:
        resp = await self._redis.xreadgroup(
            groupname=self._group,
            consumername=self._consumer_name,
            streams=streams,
            count=self._batch,
            block=block_ms,
        )
        if not resp:
            return []
        _, messages = resp[0]
        return messages

    async def _process(self, messages: list[tuple[str, dict]]) -> None:
        for msg_id, fields in messages:
            event = from_stream_fields(fields)
            self._correlator.ingest(event)
            await self._redis.xack(self._events_stream, self._group, msg_id)

    async def _emit_incidents(self, incidents: list[IncidentEvent]) -> None:
        for incident in incidents:
            logger.info("incident closed: %s", incident.model_dump_json())
            await self._redis.xadd(self._incidents_stream, {"incident": incident.model_dump_json()})


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    consumer = Consumer(redis, Correlator())
    try:
        await consumer.run_forever()
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())

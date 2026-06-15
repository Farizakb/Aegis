from datetime import datetime, timezone
from uuid import uuid4

import fakeredis.aioredis as fakeredis_aio
import pytest

from stream.consumer import Consumer, Correlator, INCIDENTS_STREAM
from stream.producer import Producer
from stream.schema import FaultKind, RawEvent, Severity


def make_event(dedup_key: str) -> RawEvent:
    return RawEvent(
        event_id=str(uuid4()),
        fault_kind=FaultKind.memory_leak,
        severity=Severity.warning,
        source="mock_app",
        message="rss high: 50MB",
        metric=50.0,
        ts=datetime.now(timezone.utc),
        dedup_key=dedup_key,
    )


@pytest.mark.integration
async def test_consumer_reads_acks_and_emits_incident():
    redis = fakeredis_aio.FakeRedis(decode_responses=True)
    producer = Producer(redis)

    await producer.publish(make_event("k1"))
    await producer.publish(make_event("k2"))

    # idle_gap_s=0 forces the window to close as soon as it is swept
    correlator = Correlator(dedup_ttl_s=5, idle_gap_s=0, max_window_s=30)
    consumer = Consumer(redis, correlator, block_ms=100)

    await consumer.ensure_group()
    incidents = await consumer.run_once()

    assert len(incidents) == 1
    incident = incidents[0]
    assert incident.fault_kind == FaultKind.memory_leak
    assert incident.event_count == 2

    assert await redis.xlen(INCIDENTS_STREAM) == 1

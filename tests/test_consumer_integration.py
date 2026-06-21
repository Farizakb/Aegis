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


@pytest.mark.integration
async def test_backpressure_drains_pending_when_high_water_exceeded():
    """When pending messages exceed PENDING_HIGH_WATER, run_once re-reads and acks them."""
    redis = fakeredis_aio.FakeRedis(decode_responses=True)
    producer = Producer(redis)
    stream = "aegis:events"
    group = "aegis:agent"
    consumer_name = "c1"

    # Publish 4 events
    for i in range(4):
        await producer.publish(make_event(f"bp-{i}"))

    # Create group, then read messages WITHOUT acking — simulates a crash
    await redis.xgroup_create(stream, group, id="0", mkstream=True)
    await redis.xreadgroup(group, consumer_name, streams={stream: ">"}, count=10)
    # Now 4 messages are pending for c1

    pending_info = await redis.xpending(stream, group)
    assert pending_info["pending"] == 4

    # Create a Consumer with low high_water=2, so drain triggers
    correlator = Correlator(dedup_ttl_s=1, idle_gap_s=0, max_window_s=30)
    consumer = Consumer(
        redis, correlator,
        consumer_name=consumer_name,
        pending_high_water=2,
        block_ms=100,
    )

    # run_once should: detect 4 pending > high_water 2, drain them, then sweep
    incidents = await consumer.run_once()

    assert len(incidents) == 1
    assert incidents[0].event_count == 4

    # Pending should be 0 after drain
    pending_after = await redis.xpending(stream, group)
    assert pending_after["pending"] == 0

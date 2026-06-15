from datetime import datetime, timedelta, timezone
from uuid import uuid4

from stream.consumer import Correlator
from stream.schema import FaultKind, RawEvent, Severity


class Clock:
    def __init__(self, start: datetime):
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def make_event(ts, *, fault_kind=FaultKind.memory_leak, source="mock_app",
                severity=Severity.warning, dedup_key="k", metric=50.0):
    return RawEvent(
        event_id=str(uuid4()),
        fault_kind=fault_kind,
        severity=severity,
        source=source,
        message="m",
        metric=metric,
        ts=ts,
        dedup_key=dedup_key,
    )


def test_dedup_collapses_repeats_into_single_event():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    clock = Clock(start)
    correlator = Correlator(dedup_ttl_s=5, idle_gap_s=3, max_window_s=30, now_fn=clock)

    for i in range(5):
        correlator.ingest(make_event(start + timedelta(seconds=i), dedup_key="same"))
        clock.advance(1)

    clock.advance(4)  # idle past idle_gap_s
    incidents = correlator.sweep()

    assert len(incidents) == 1
    assert incidents[0].event_count == 1
    assert incidents[0].duplicate_count == 4


def test_burst_then_idle_closes_one_incident_with_all_events():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    clock = Clock(start)
    correlator = Correlator(dedup_ttl_s=1, idle_gap_s=3, max_window_s=30, now_fn=clock)

    for i in range(3):
        correlator.ingest(make_event(start + timedelta(seconds=i), dedup_key=f"k{i}"))
        clock.advance(1)

    clock.advance(4)
    incidents = correlator.sweep()

    assert len(incidents) == 1
    incident = incidents[0]
    assert incident.event_count == 3
    assert incident.duplicate_count == 0
    assert incident.first_seen == start
    assert incident.last_seen == start + timedelta(seconds=2)
    assert len(incident.sample_events) == 3


def test_two_fault_kinds_open_separate_incidents():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    clock = Clock(start)
    correlator = Correlator(dedup_ttl_s=1, idle_gap_s=3, max_window_s=30, now_fn=clock)

    correlator.ingest(make_event(start, fault_kind=FaultKind.memory_leak, dedup_key="ml"))
    correlator.ingest(make_event(start, fault_kind=FaultKind.db_deadlock, dedup_key="dl"))

    clock.advance(4)
    incidents = correlator.sweep()

    assert len(incidents) == 2
    assert {incident.fault_kind for incident in incidents} == {
        FaultKind.memory_leak,
        FaultKind.db_deadlock,
    }


def test_max_window_force_closes_continuously_active_window():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    clock = Clock(start)
    correlator = Correlator(dedup_ttl_s=0, idle_gap_s=100, max_window_s=10, now_fn=clock)

    correlator.ingest(make_event(start, dedup_key="a"))
    clock.advance(5)
    correlator.ingest(make_event(start + timedelta(seconds=5), dedup_key="b"))
    clock.advance(6)  # opened_at age = 11s > max_window_s=10, idle_elapsed = 6s < idle_gap_s=100

    incidents = correlator.sweep()

    assert len(incidents) == 1
    assert incidents[0].event_count == 2


def test_no_incidents_while_window_still_open():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    clock = Clock(start)
    correlator = Correlator(dedup_ttl_s=1, idle_gap_s=3, max_window_s=30, now_fn=clock)

    correlator.ingest(make_event(start, dedup_key="a"))
    clock.advance(1)

    assert correlator.sweep() == []

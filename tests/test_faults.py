import pytest

from mock_app.faults.db_deadlock import DbDeadlockFault
from mock_app.faults.error_spike import ErrorSpikeFault
from mock_app.faults.memory_leak import MemoryLeakFault
from stream.schema import FaultKind, Severity


class FakeProducer:
    def __init__(self):
        self.published = []

    async def publish(self, event):
        self.published.append(event)


def test_list_faults_initial(client):
    resp = client.get("/faults")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {k.value for k in FaultKind}
    assert all(active is False for active in data.values())


def test_trigger_and_clear(client):
    resp = client.post("/faults/memory_leak/trigger")
    assert resp.status_code == 200
    assert resp.json() == {"kind": "memory_leak", "active": True}
    assert client.get("/faults").json()["memory_leak"] is True

    resp = client.post("/faults/memory_leak/clear")
    assert resp.status_code == 200
    assert resp.json() == {"kind": "memory_leak", "active": False}
    assert client.get("/faults").json()["memory_leak"] is False


def test_trigger_unknown_kind_returns_404(client):
    resp = client.post("/faults/not_a_fault/trigger")
    assert resp.status_code == 404


def test_trigger_already_active_returns_409(client):
    client.post("/faults/db_deadlock/trigger")
    resp = client.post("/faults/db_deadlock/trigger")
    assert resp.status_code == 409


async def test_memory_leak_emits_warning_signal():
    fault = MemoryLeakFault()
    producer = FakeProducer()

    await fault.trigger()
    await fault.emit_signals(producer)

    assert len(producer.published) == 1
    event = producer.published[0]
    assert event.fault_kind == FaultKind.memory_leak
    assert event.severity == Severity.warning
    assert event.metric == 5.0
    assert event.dedup_key


async def test_memory_leak_inactive_emits_nothing():
    fault = MemoryLeakFault()
    producer = FakeProducer()

    await fault.emit_signals(producer)

    assert producer.published == []


async def test_db_deadlock_emits_on_alternate_ticks():
    fault = DbDeadlockFault()
    producer = FakeProducer()

    await fault.trigger()
    await fault.emit_signals(producer)  # tick 1: no emit
    await fault.emit_signals(producer)  # tick 2: emit

    assert len(producer.published) == 1
    event = producer.published[0]
    assert event.fault_kind == FaultKind.db_deadlock
    assert event.severity == Severity.error


async def test_error_spike_should_fail_only_when_active():
    fault = ErrorSpikeFault()

    assert fault.should_fail() is False  # inactive

    await fault.trigger()
    results = [fault.should_fail() for _ in range(50)]

    assert any(results)  # ERROR_RATE=0.5 -> almost certainly at least one failure
    assert not all(results)  # ...and at least one success


async def test_error_spike_emits_error_rate_metric():
    fault = ErrorSpikeFault()
    producer = FakeProducer()

    await fault.trigger()
    for _ in range(10):
        fault.should_fail()
    await fault.emit_signals(producer)

    assert len(producer.published) == 1
    event = producer.published[0]
    assert event.fault_kind == FaultKind.error_spike
    assert event.metric is not None

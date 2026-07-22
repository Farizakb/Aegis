import fakeredis.aioredis as fakeredis_aio
import pytest
import redis.asyncio as redis_asyncio
from fastapi.testclient import TestClient

import mock_app.main as main_module
from mock_app.controls import FLAGS, RISKY_FLAG, SCALE
from mock_app.faults import FAULTS


@pytest.fixture
def fake_redis():
    return fakeredis_aio.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
async def reset_faults():
    for fault in FAULTS.values():
        await fault.clear()
    FLAGS.set(RISKY_FLAG, False)
    SCALE.set_workers(2)
    yield
    for fault in FAULTS.values():
        await fault.clear()
    FLAGS.set(RISKY_FLAG, False)
    SCALE.set_workers(2)


@pytest.fixture(autouse=True)
def reset_tracer_provider():
    """Snapshot + restore the global OTel tracer provider around every test.

    Observability tests point the global provider at an InMemorySpanExporter by
    assigning ``_trace._TRACER_PROVIDER`` (and ``observability.tracing._provider``)
    directly, bypassing OTel's set-once guard. Without this, that in-memory
    provider leaks into later tests. For non-obs tests the snapshot equals the
    restore, so this is a no-op.
    """
    import observability.tracing as _obs_tracing
    import opentelemetry.trace as _otel_trace

    saved_global = _otel_trace._TRACER_PROVIDER
    saved_module = _obs_tracing._provider
    yield
    _otel_trace._TRACER_PROVIDER = saved_global
    _obs_tracing._provider = saved_module


@pytest.fixture
def client(monkeypatch, fake_redis):
    monkeypatch.setattr(redis_asyncio.Redis, "from_url", classmethod(lambda cls, *a, **k: fake_redis))
    with TestClient(main_module.app) as c:
        yield c

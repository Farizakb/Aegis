from observability import tracing


def test_setup_tracing_is_idempotent_and_returns_provider():
    p1 = tracing.setup_tracing("aegis-test")
    p2 = tracing.setup_tracing("aegis-test")
    assert p1 is p2  # global, set up once
    assert tracing.get_tracer() is not None


import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry import trace as _trace


@pytest.fixture
def span_exporter(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Point the module's tracer + global at this provider for the test.
    monkeypatch.setattr(tracing, "_provider", provider)
    _trace._TRACER_PROVIDER = provider  # force get_tracer() to use it
    return exporter


class _Incident:
    incident_id = "inc-1"
    class fault_kind:  # noqa: N801 - stub with .value
        value = "memory_leak"


async def test_traced_decorator_opens_span_with_incident_attrs(span_exporter):
    @tracing.traced("graph.demo")
    async def node(state, extra=None):
        return {"ok": True, "extra": extra}

    result = await node({"incident": _Incident()}, extra="x")
    assert result == {"ok": True, "extra": "x"}

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "graph.demo"
    assert spans[0].attributes["incident_id"] == "inc-1"
    assert spans[0].attributes["fault_kind"] == "memory_leak"


async def test_traced_decorator_logs_the_node_with_incident_context(span_exporter):
    """Each traced node emits one log line, so the lifecycle is visible in the
    terminal and cross-references the trace (spec section 11)."""
    import structlog

    @tracing.traced("graph.demo")
    async def node(state):
        return {"ok": True}

    with structlog.testing.capture_logs() as logs:
        await node({"incident": _Incident()})

    assert [entry["event"] for entry in logs] == ["graph.demo"]
    assert logs[0]["incident_id"] == "inc-1"
    assert logs[0]["fault_kind"] == "memory_leak"


async def test_traced_decorator_logs_even_without_an_incident(span_exporter):
    import structlog

    @tracing.traced("graph.demo")
    async def node(state):
        return {"ok": True}

    with structlog.testing.capture_logs() as logs:
        await node({})

    assert [entry["event"] for entry in logs] == ["graph.demo"]
    assert "incident_id" not in logs[0]

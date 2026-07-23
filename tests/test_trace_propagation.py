import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry import trace as _trace
from opentelemetry.propagate import set_global_textmap
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from observability import tracing


@pytest.fixture
def tracer_provider():
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(InMemorySpanExporter()))
    _trace._TRACER_PROVIDER = provider
    set_global_textmap(TraceContextTextMapPropagator())
    return provider


def test_traceparent_round_trip_preserves_trace_id(tracer_provider):
    tracer = tracing.get_tracer()
    with tracer.start_as_current_span("consumer.correlate") as span:
        producer_trace_id = span.get_span_context().trace_id
        carrier = tracing.inject_trace_context()

    assert "traceparent" in carrier

    ctx = tracing.extract_trace_context(carrier)
    with tracer.start_as_current_span("agent.process", context=ctx) as child:
        assert child.get_span_context().trace_id == producer_trace_id

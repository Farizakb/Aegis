import structlog
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry import trace as _trace

from observability.logging import setup_logging, add_trace_context
from observability import tracing


def test_log_line_carries_incident_id_and_trace_id():
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(InMemorySpanExporter()))
    _trace._TRACER_PROVIDER = provider
    setup_logging()

    # capture_logs() clears the ENTIRE configured processor chain (see structlog
    # source), so both contextvars merging and trace-context stamping must be
    # explicitly re-added via the `processors=` kwarg (available since structlog
    # 25.5.0) to observe them on the captured line.
    with structlog.testing.capture_logs(
        processors=(structlog.contextvars.merge_contextvars, add_trace_context)
    ) as caps:
        structlog.contextvars.bind_contextvars(incident_id="inc-9")
        try:
            with tracing.get_tracer().start_as_current_span("x"):
                structlog.get_logger("t").info("hello")
        finally:
            structlog.contextvars.clear_contextvars()

    line = caps[0]
    assert line["incident_id"] == "inc-9"
    assert "trace_id" in line and len(line["trace_id"]) == 32


def test_add_trace_context_stamps_active_span():
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(InMemorySpanExporter()))
    _trace._TRACER_PROVIDER = provider
    from observability.logging import add_trace_context
    with tracing.get_tracer().start_as_current_span("x"):
        out = add_trace_context(None, "info", {})
    assert len(out["trace_id"]) == 32 and len(out["span_id"]) == 16

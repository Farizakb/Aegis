"""OpenTelemetry tracing setup and helpers (ADR-0009).

One trace per incident, rooted at consumer.correlate and propagated across the
Redis queue boundary via W3C traceparent. Exporter is OTLP/HTTP to Jaeger;
absence of Jaeger degrades gracefully (spans are dropped, no crash).
"""

from __future__ import annotations

import functools
import logging
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract, inject, set_global_textmap
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

DEFAULT_OTLP_ENDPOINT = "http://localhost:4318"
_provider: TracerProvider | None = None


def setup_tracing(service_name: str) -> TracerProvider:
    """Install a global TracerProvider exporting to OTLP/HTTP. Idempotent."""
    global _provider
    if _provider is not None:
        return _provider

    # Keep export failures quiet so running without Jaeger doesn't spew errors.
    logging.getLogger("opentelemetry.exporter.otlp.proto.http.trace_exporter").setLevel(
        logging.CRITICAL
    )

    base = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", DEFAULT_OTLP_ENDPOINT).rstrip("/")
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces")))
    trace.set_tracer_provider(provider)
    set_global_textmap(TraceContextTextMapPropagator())
    _provider = provider
    return provider


def get_tracer() -> trace.Tracer:
    return trace.get_tracer("aegis")


def shutdown_tracing() -> None:
    """Force-flush + shutdown so short-lived processes export their spans.

    When Jaeger is absent the OTLP exporter retries with bounded backoff, so
    this force-flush can briefly delay process exit; the exporter's abort event
    caps that wait, so it is a bounded pause, never an indefinite hang.
    """
    global _provider
    if _provider is not None:
        _provider.shutdown()
        _provider = None


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def traced(span_name: str):
    """Wrap an async graph node in a span, stamping incident_id/fault_kind."""

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(state, *args, **kwargs):
            with get_tracer().start_as_current_span(span_name) as span:
                incident = state.get("incident") if isinstance(state, dict) else None
                if incident is not None:
                    span.set_attribute("incident_id", incident.incident_id)
                    span.set_attribute("fault_kind", incident.fault_kind.value)
                return await func(state, *args, **kwargs)

        return wrapper

    return decorator


def inject_trace_context() -> dict[str, str]:
    """Serialize the current context into a W3C carrier (traceparent/tracestate)."""
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier


def extract_trace_context(carrier: dict):
    """Rebuild a context from a W3C carrier read off the stream."""
    return extract(carrier)

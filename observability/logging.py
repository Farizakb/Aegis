"""structlog configuration: every line carries incident_id + trace_id (ADR-0009)."""

from __future__ import annotations

import logging
import os

import structlog
from opentelemetry import trace


def add_trace_context(logger, method_name, event_dict):
    """structlog processor: stamp the active span's trace_id/span_id onto the line."""
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def setup_logging() -> None:
    json_mode = os.environ.get("AEGIS_LOG_JSON", "").lower() in ("1", "true", "yes")
    renderer = (
        structlog.processors.JSONRenderer()
        if json_mode
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            add_trace_context,
            structlog.processors.TimeStamper(fmt="iso"),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

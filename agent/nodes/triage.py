"""Triage node: classify the incident and propose a root-cause hypothesis."""

from __future__ import annotations

import time

from opentelemetry import trace

from agent.state import AgentState, NodeUsage, TriageResult
from observability.tracing import traced
from stream.schema import IncidentEvent

MAX_SAMPLE_EVENTS = 5


def _prompt(incident: IncidentEvent) -> str:
    samples = "\n".join(
        f"- [{e.severity.value}] {e.message} (metric={e.metric})"
        for e in incident.sample_events[:MAX_SAMPLE_EVENTS]
    )
    return (
        "You are an SRE triage assistant. Analyze the following incident and identify the "
        "most likely root cause.\n\n"
        f"Fault kind (reported by pipeline): {incident.fault_kind.value}\n"
        f"Severity: {incident.severity.value}\n"
        f"Source: {incident.source}\n"
        f"Title: {incident.title}\n"
        f"Summary: {incident.summary}\n"
        f"Event count: {incident.event_count} (duplicates suppressed: {incident.duplicate_count})\n"
        f"Window: {incident.first_seen} - {incident.last_seen} "
        f"({incident.correlation_window_s}s)\n\n"
        f"Sample events:\n{samples}"
    )


@traced("graph.triage")
async def triage_node(state: AgentState, llm) -> dict:
    structured = llm.with_structured_output(TriageResult, include_raw=True)
    start = time.monotonic()
    response = await structured.ainvoke(_prompt(state["incident"]))
    latency_ms = (time.monotonic() - start) * 1000

    usage_metadata = getattr(response["raw"], "usage_metadata", None) or {}
    usage = NodeUsage(
        node="triage",
        model=getattr(llm, "model", "unknown"),
        input_tokens=usage_metadata.get("input_tokens", 0),
        output_tokens=usage_metadata.get("output_tokens", 0),
        latency_ms=latency_ms,
    )
    parsed = response["parsed"]
    if parsed is not None:
        trace.get_current_span().set_attribute("triage.confidence", parsed.confidence)
    return {"triage": parsed, "usage": state["usage"] + [usage]}

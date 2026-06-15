"""Triage node: classify the incident and propose a root-cause hypothesis."""

from __future__ import annotations

from agent.state import AgentState, TriageResult
from stream.schema import IncidentEvent


def _prompt(incident: IncidentEvent) -> str:
    samples = "\n".join(
        f"- [{e.severity.value}] {e.message} (metric={e.metric})" for e in incident.sample_events
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


async def triage_node(state: AgentState, llm) -> dict:
    structured = llm.with_structured_output(TriageResult)
    result = await structured.ainvoke(_prompt(state["incident"]))
    return {"triage": result}

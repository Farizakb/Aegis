"""Report node: emit a final IncidentReport summarizing the run outcome."""

from __future__ import annotations

from agent.state import (
    AgentState,
    HitlChoice,
    IncidentReport,
    Outcome,
    PolicyDecision,
)


def _determine_outcome(state: AgentState) -> Outcome:
    hitl = state.get("hitl_decision")
    if hitl and hitl.choice == HitlChoice.approve:
        return Outcome.applied
    if hitl and hitl.choice == HitlChoice.reject:
        return Outcome.rejected
    verdict = state.get("policy_verdict")
    if verdict and verdict.decision == PolicyDecision.block:
        return Outcome.blocked
    return Outcome.escalated


async def report_node(state: AgentState, sink) -> dict:
    incident = state["incident"]
    triage = state.get("triage")
    sandbox = state.get("sandbox_result")
    verdict = state.get("policy_verdict")
    hitl = state.get("hitl_decision")
    fix = state.get("proposed_fix")

    total_in = sum(u.input_tokens for u in state.get("usage", []))
    total_out = sum(u.output_tokens for u in state.get("usage", []))
    total_lat = sum(u.latency_ms for u in state.get("usage", []))

    outcome = _determine_outcome(state)
    report = IncidentReport(
        incident_id=incident.incident_id,
        fault_kind=incident.fault_kind,
        outcome=outcome,
        triage_confidence=triage.confidence if triage else None,
        retries=state.get("retries", 0),
        sandbox_passed=sandbox.passed if sandbox else None,
        policy_decision=verdict.decision if verdict else None,
        hitl_choice=hitl.choice if hitl else None,
        applied_target_file=fix.target_file if fix and outcome == Outcome.applied else None,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        total_latency_ms=total_lat,
    )
    sink.emit(report)
    return {"report": report}

"""Report node: emit the final IncidentReport; file the durable fix (ADR-0003)."""

from __future__ import annotations

from opentelemetry import trace

from agent.actions import ActionType
from agent.state import (
    AgentState, HitlChoice, IncidentReport, Outcome, PolicyDecision,
)
from observability.tracing import traced


def _determine_outcome(state: AgentState) -> Outcome:
    if state.get("applied"):
        return Outcome.applied
    hitl = state.get("hitl_decision")
    if hitl and hitl.choice is HitlChoice.reject:
        return Outcome.rejected
    verdict = state.get("policy_verdict")
    if verdict and verdict.decision == PolicyDecision.block:
        return Outcome.blocked
    # escalate action, retry exhaustion, HITL expiry, and apply failure all land here
    return Outcome.escalated


@traced("report.persist")
async def report_node(state: AgentState, sink, registry) -> dict:
    incident = state["incident"]
    triage = state.get("triage")
    sandbox = state.get("sandbox_result")
    verdict = state.get("policy_verdict")
    hitl = state.get("hitl_decision")
    plan = state.get("plan")
    mitigation = plan.mitigation if plan else None
    durable_fix = plan.durable_fix if plan else None

    outcome = _determine_outcome(state)
    usage = state.get("usage", [])
    # File the durable fix regardless of terminal outcome (§5 "open durable fixes"):
    # a rejected/blocked/escalated incident still has an unfixed root cause worth ticketing.
    if durable_fix is not None:
        registry.file(incident=incident, triage=triage, fix=durable_fix)

    applied_target_file = (
        mitigation.target_file
        if outcome is Outcome.applied and mitigation
        and mitigation.action is ActionType.patch_code else None
    )
    report = IncidentReport(
        incident_id=incident.incident_id,
        fault_kind=incident.fault_kind,
        outcome=outcome,
        mitigation_action=mitigation.action if mitigation else None,
        attempted_actions=[a.action.action.value for a in state.get("attempted", [])],
        durable_fix=durable_fix,
        triage_confidence=triage.confidence if triage else None,
        retries=state.get("retries", 0),
        sandbox_passed=sandbox.passed if sandbox else None,
        policy_decision=verdict.decision if verdict else None,
        hitl_choice=hitl.choice if hitl else None,
        apply_error=state.get("apply_error"),
        applied_target_file=applied_target_file,
        total_input_tokens=sum(u.input_tokens for u in usage),
        total_output_tokens=sum(u.output_tokens for u in usage),
        total_latency_ms=sum(u.latency_ms for u in usage),
        node_usage=list(usage),
    )
    sink.emit(report)
    trace.get_current_span().set_attribute("report.outcome", report.outcome.value)
    return {"report": report}

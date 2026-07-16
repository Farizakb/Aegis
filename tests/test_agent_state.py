from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent.actions import ActionType, ProposedAction
from agent.state import (
    AgentState, AttemptRecord, HitlChoice, IncidentReport, NodeUsage, Outcome,
    RemediationPlan, RetrievedChunk, SandboxResult, TriageResult, initial_state,
)
from stream.schema import FaultKind, IncidentEvent, Severity


def make_incident() -> IncidentEvent:
    now = datetime.now(timezone.utc)
    return IncidentEvent(
        incident_id="inc-1",
        fault_kind=FaultKind.memory_leak,
        severity=Severity.critical,
        source="mock_app",
        title="memory_leak on mock_app",
        summary="rss climbed to 200MB",
        first_seen=now,
        last_seen=now,
        event_count=3,
        duplicate_count=5,
        sample_events=[],
        correlation_window_s=3.0,
    )


def _action() -> ProposedAction:
    return ProposedAction(action=ActionType.restart_service, reason="leak")


def _result(passed: bool = False) -> SandboxResult:
    return SandboxResult(passed=passed, exit_code=0 if passed else 1,
                         stdout="", stderr="", duration_ms=1.0)


def test_triage_result_validates():
    triage = TriageResult(
        fault_kind=FaultKind.memory_leak,
        root_cause="unbounded list growth in MemoryLeakFault",
        confidence=0.9,
        reasoning="rss grows linearly with each tick",
    )
    assert triage.confidence == 0.9


def test_triage_result_rejects_out_of_range_confidence():
    with pytest.raises(ValidationError):
        TriageResult(
            fault_kind=FaultKind.memory_leak,
            root_cause="x",
            confidence=1.5,
            reasoning="x",
        )


def test_retrieved_chunk_validates():
    chunk = RetrievedChunk(source="runbook:memory_leak.md", content="...", score=0.83)
    assert chunk.score == 0.83


def test_incident_event_is_the_agent_input():
    incident = make_incident()
    assert incident.fault_kind == FaultKind.memory_leak


def test_node_usage_validates():
    usage = NodeUsage(
        node="triage",
        model="claude-haiku-4-5-20251001",
        input_tokens=120,
        output_tokens=40,
        latency_ms=850.5,
    )
    assert usage.node == "triage"
    assert usage.input_tokens == 120


def test_sandbox_result_validates():
    r = SandboxResult(passed=True, exit_code=0, stdout="1 passed", stderr="", duration_ms=1200.0)
    assert r.passed is True
    assert r.exit_code == 0


def test_policy_verdict_validates():
    from agent.state import PolicyDecision, PolicyVerdict

    v = PolicyVerdict(
        decision=PolicyDecision.needs_approval,
        violated_rules=["blast_radius"],
        reasons=["target outside auto-approved paths"],
    )
    assert v.decision == PolicyDecision.needs_approval
    assert len(v.violated_rules) == 1


def test_hitl_decision_validates():
    from agent.state import HitlDecision

    d = HitlDecision(choice=HitlChoice.approve, decided_by="local-ui", note="looks good")
    assert d.choice == HitlChoice.approve


def test_remediation_plan_mitigation_only():
    plan = RemediationPlan(mitigation=_action())
    assert plan.durable_fix is None


def test_remediation_plan_with_durable_fix():
    plan = RemediationPlan(
        mitigation=_action(),
        durable_fix=ProposedAction(
            action=ActionType.patch_code, reason="fix the leak",
            patch="--- a/x\n+++ b/x\n", target_file="mock_app/faults/memory_leak.py"),
    )
    assert plan.durable_fix.action is ActionType.patch_code


def test_attempt_record_pairs_action_with_evidence():
    rec = AttemptRecord(action=_action(), result=_result())
    assert rec.action.action is ActionType.restart_service
    assert rec.result.passed is False


def test_hitl_choice_has_expired():
    assert HitlChoice.expired.value == "expired"


def test_incident_report_validates():
    r = IncidentReport(
        incident_id="inc-1",
        fault_kind=FaultKind.memory_leak,
        outcome=Outcome.applied,
        retries=1,
        sandbox_passed=True,
        total_input_tokens=500,
        total_output_tokens=100,
        total_latency_ms=3200.0,
    )
    assert r.outcome == Outcome.applied
    assert r.retries == 1


def test_incident_report_v2_fields_default():
    report = IncidentReport(incident_id="i-1", fault_kind=FaultKind.memory_leak,
                            outcome=Outcome.escalated)
    assert report.mitigation_action is None
    assert report.attempted_actions == []
    assert report.durable_fix is None
    assert report.apply_error is None


def test_proposed_fix_is_gone():
    import agent.state
    assert not hasattr(agent.state, "ProposedFix")


def test_initial_state_defaults():
    state = initial_state(make_incident())
    assert state["plan"] is None
    assert state["attempted"] == []
    assert state["applied"] is False
    assert state["apply_error"] is None
    assert state["retries"] == 0


def test_initial_state_promoted_run_presets_plan():
    plan = RemediationPlan(mitigation=_action())
    state = initial_state(make_incident(), plan=plan)
    assert state["plan"] is plan

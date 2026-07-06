from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent.state import NodeUsage, ProposedFix, RetrievedChunk, TriageResult
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


def test_retrieved_chunk_and_proposed_fix_roundtrip():
    chunk = RetrievedChunk(source="runbook:memory_leak.md", content="...", score=0.83)
    fix = ProposedFix(
        description="cap memory growth",
        patch="--- a/mock_app/faults/memory_leak.py\n+++ b/mock_app/faults/memory_leak.py\n",
        target_file="mock_app/faults/memory_leak.py",
    )

    assert chunk.score == 0.83
    assert fix.target_file == "mock_app/faults/memory_leak.py"


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
    from agent.state import SandboxResult

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
    from agent.state import HitlChoice, HitlDecision

    d = HitlDecision(choice=HitlChoice.approve, decided_by="local-ui", note="looks good")
    assert d.choice == HitlChoice.approve


def test_incident_report_validates():
    from agent.state import Outcome, IncidentReport

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


from agent.actions import ActionType, ProposedAction
from agent.state import RemediationPlan


def test_remediation_plan_mitigation_only():
    plan = RemediationPlan(
        mitigation=ProposedAction(action=ActionType.restart_service, reason="clear leak"))
    assert plan.durable_fix is None


def test_remediation_plan_with_durable_fix():
    plan = RemediationPlan(
        mitigation=ProposedAction(action=ActionType.restart_service, reason="clear leak"),
        durable_fix=ProposedAction(
            action=ActionType.patch_code, reason="fix the leak",
            patch="--- a/x\n+++ b/x\n", target_file="mock_app/faults/memory_leak.py"),
    )
    assert plan.durable_fix.action is ActionType.patch_code

# tests/test_agent_graph.py
from datetime import datetime, timezone

from agent.graph import build_graph
from agent.state import (
    HitlChoice,
    HitlDecision,
    Outcome,
    PolicyDecision,
    PolicyVerdict,
    ProposedFix,
    SandboxResult,
    TriageResult,
)
from stream.schema import FaultKind, IncidentEvent, Severity
from tests.test_agent_nodes import FakeLLM, FakeSearchTool
from tests.test_sandbox_node import FakeExecutor
from tests.test_hitl_gate import FakeGate
from tests.test_report_node import FakeSink


class FakeApplier:
    def __init__(self):
        self.applied = []

    def apply(self, *, target_file, patch):
        self.applied.append((target_file, patch))


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


def _init_state():
    return {
        "incident": make_incident(),
        "triage": None,
        "retrieved_context": [],
        "proposed_fix": None,
        "retries": 0,
        "sandbox_result": None,
        "policy_verdict": None,
        "hitl_decision": None,
        "report": None,
        "usage": [],
    }


def _build(sandbox_result, policy_engine, hitl_decision, applier=None):
    triage_result = TriageResult(
        fault_kind=FaultKind.memory_leak,
        root_cause="unbounded list growth",
        confidence=0.9,
        reasoning="rss grows linearly",
    )
    proposed_fix = ProposedFix(
        description="cap memory",
        patch="+cap = 100\n",
        target_file="mock_app/faults/memory_leak.py",
    )
    llm = FakeLLM({TriageResult: triage_result, ProposedFix: proposed_fix})
    search_tool = FakeSearchTool([{"source": "runbook:memory_leak.md", "content": "cap list", "score": 0.8}])
    executor = FakeExecutor(sandbox_result)
    gate = FakeGate(hitl_decision) if hitl_decision else FakeGate(HitlDecision(choice=HitlChoice.reject))
    sink = FakeSink()
    if applier is None:
        applier = FakeApplier()

    graph = build_graph(llm, llm, search_tool, executor, policy_engine, gate, applier, sink)
    return graph, sink, applier


async def test_happy_path_sandbox_pass_policy_needs_approval_hitl_approve():
    from policy.engine import PolicyEngine
    # patch_code is always at least needs_approval (irreversibility_gate) even
    # with a passing sandbox and high-confidence triage — never auto-allowed.
    sandbox_ok = SandboxResult(passed=True, exit_code=0, stdout="1 passed", stderr="", duration_ms=100)
    decision = HitlDecision(choice=HitlChoice.approve)
    graph, sink, applier = _build(sandbox_ok, PolicyEngine(), decision)

    result = await graph.ainvoke(_init_state())

    assert result["policy_verdict"].decision == PolicyDecision.needs_approval
    assert "irreversibility_gate" in result["policy_verdict"].violated_rules
    assert result["report"].outcome == Outcome.applied
    assert len(applier.applied) == 1


async def test_sandbox_fail_retries_then_escalates():
    from policy.engine import PolicyEngine
    sandbox_fail = SandboxResult(passed=False, exit_code=1, stdout="", stderr="fail", duration_ms=100)
    graph, sink, _ = _build(sandbox_fail, PolicyEngine(), None)

    result = await graph.ainvoke(_init_state())

    assert result["report"].outcome == Outcome.escalated
    assert result["retries"] == 2


async def test_policy_block_skips_hitl():
    from policy.engine import PolicyEngine

    class BlockEngine:
        def evaluate(self, **kwargs):
            return PolicyVerdict(decision=PolicyDecision.block, violated_rules=["protected_path"])

    sandbox_ok = SandboxResult(passed=True, exit_code=0, stdout="", stderr="", duration_ms=100)
    graph, sink, applier = _build(sandbox_ok, BlockEngine(), None)

    result = await graph.ainvoke(_init_state())

    assert result["report"].outcome == Outcome.blocked
    assert result["hitl_decision"] is None
    assert len(applier.applied) == 0


async def test_apply_records_with_engine_after_success():
    class RecordingEngine:
        def __init__(self):
            self.calls = []

        def evaluate(self, **kwargs):
            return PolicyVerdict(decision=PolicyDecision.needs_approval, violated_rules=["irreversibility_gate"])

        def record_apply(self, proposal, decision):
            self.calls.append((proposal.action.value, decision))

    sandbox_ok = SandboxResult(passed=True, exit_code=0, stdout="", stderr="", duration_ms=100)
    decision = HitlDecision(choice=HitlChoice.approve)
    engine = RecordingEngine()
    graph, sink, applier = _build(sandbox_ok, engine, decision)

    result = await graph.ainvoke(_init_state())

    assert result["report"].outcome == Outcome.applied
    assert engine.calls == [("patch_code", PolicyDecision.needs_approval)]


async def test_hitl_reject_skips_apply():
    from policy.engine import PolicyEngine
    sandbox_ok = SandboxResult(passed=True, exit_code=0, stdout="", stderr="", duration_ms=100)
    decision = HitlDecision(choice=HitlChoice.reject, note="looks risky")
    graph, sink, applier = _build(sandbox_ok, PolicyEngine(), decision)

    result = await graph.ainvoke(_init_state())

    assert result["report"].outcome == Outcome.rejected
    assert len(applier.applied) == 0

# tests/test_agent_graph.py
"""Full-graph e2e coverage: real PolicyEngine, faked LLM/executor/gate/applier/sink/registry."""
from __future__ import annotations

from dataclasses import dataclass, field

from agent.actions import ActionType, ProposedAction
from agent.graph import build_graph, route_after_policy, route_after_sandbox
from agent.state import (
    HitlChoice,
    HitlDecision,
    Outcome,
    PolicyDecision,
    PolicyVerdict,
    RemediationPlan,
    SandboxResult,
    initial_state,
)
from policy.engine import PolicyEngine
from tests.test_agent_nodes import FakeSearchTool, FakeStructuredLLM, make_incident, make_triage
from tests.test_report_node import FakeRegistry, FakeSink


def _result(passed: bool) -> SandboxResult:
    return SandboxResult(passed=passed, exit_code=0 if passed else 1,
                         stdout="", stderr="", duration_ms=1.0)


def test_route_sandbox_none_goes_to_policy():
    state = {"sandbox_result": None, "retries": 0}
    assert route_after_sandbox(state) == "policy"


def test_route_sandbox_pass_goes_to_policy():
    state = {"sandbox_result": _result(True), "retries": 0}
    assert route_after_sandbox(state) == "policy"


def test_route_sandbox_fail_retries_then_reports():
    assert route_after_sandbox({"sandbox_result": _result(False), "retries": 1}) == "propose"
    assert route_after_sandbox({"sandbox_result": _result(False), "retries": 2}) == "report"


def _policy_state(decision, action=ActionType.restart_service):
    return {
        "policy_verdict": PolicyVerdict(decision=decision),
        "plan": RemediationPlan(mitigation=ProposedAction(action=action, reason="t")),
    }


def test_route_policy_block_reports():
    assert route_after_policy(_policy_state(PolicyDecision.block)) == "report"


def test_route_policy_escalate_reports_even_when_allowed():
    assert route_after_policy(
        _policy_state(PolicyDecision.allow, action=ActionType.escalate)) == "report"


def test_route_policy_allow_auto_applies():
    assert route_after_policy(_policy_state(PolicyDecision.allow)) == "apply"


def test_route_policy_needs_approval_goes_to_hitl():
    assert route_after_policy(_policy_state(PolicyDecision.needs_approval)) == "hitl"


# ---------------------------------------------------------------------------
# Full-graph e2e fakes
# ---------------------------------------------------------------------------

class FakeExecutor:
    """Queue of sandbox results, one per verify() call (last one repeats)."""

    def __init__(self, results: list[SandboxResult]):
        self._results = list(results)
        self.calls: list[tuple] = []

    async def verify(self, action, fault_kind):
        self.calls.append((action, fault_kind))
        idx = min(len(self.calls) - 1, len(self._results) - 1)
        return self._results[idx]


class SpyGate:
    """Records every request_approval call; returns a scripted decision."""

    def __init__(self, decision: HitlDecision):
        self._decision = decision
        self.calls: list[dict] = []

    async def request_approval(self, **kwargs):
        self.calls.append(kwargs)
        return self._decision


class RaisingGate:
    """Fails the test if the HITL gate is ever invoked (auto-apply / blocked paths)."""

    def __init__(self):
        self.calls: list[dict] = []

    async def request_approval(self, **kwargs):
        raise AssertionError("HITL gate must not be invoked for this scenario")


class RaisingLLM:
    """Fails the test if invoked — for promoted runs, which skip triage/propose."""

    model = "fake-model"

    def with_structured_output(self, schema, include_raw=True):
        raise AssertionError("LLM must not be invoked on a promoted (sandbox-entry) run")


class SpyApplier:
    def __init__(self):
        self.applied: list[ProposedAction] = []

    def apply(self, action: ProposedAction) -> None:
        self.applied.append(action)


def _plan(action=ActionType.restart_service, **kwargs) -> RemediationPlan:
    return RemediationPlan(mitigation=ProposedAction(action=action, reason="test", **kwargs))


@dataclass
class _Built:
    graph: object
    applier: SpyApplier
    sink: FakeSink
    registry: FakeRegistry
    gate: object
    executor: FakeExecutor = field(default=None)


def _graph(
    *,
    gate,
    triage_llm=None,
    propose_llm=None,
    triage_result=None,
    plan_queue=None,
    search_chunks=None,
    sandbox_results=None,
    policy_engine=None,
    applier=None,
    sink=None,
    registry=None,
) -> _Built:
    triage_llm = triage_llm or FakeStructuredLLM([triage_result or make_triage()])
    propose_llm = propose_llm or FakeStructuredLLM(plan_queue or [_plan()])
    search_tool = FakeSearchTool(search_chunks or [])
    executor = FakeExecutor(sandbox_results or [_result(True)])
    applier = applier or SpyApplier()
    sink = sink or FakeSink()
    registry = registry or FakeRegistry()

    graph = build_graph(
        triage_llm, propose_llm, search_tool, executor,
        policy_engine or PolicyEngine(), gate, applier, sink, registry,
    )
    return _Built(graph=graph, applier=applier, sink=sink, registry=registry,
                  gate=gate, executor=executor)


async def test_allow_auto_applies_and_skips_hitl():
    # restart plan, verify passes, confident triage -> allow -> apply without gate
    gate = RaisingGate()
    built = _graph(gate=gate, plan_queue=[_plan(ActionType.restart_service)],
                    sandbox_results=[_result(True)])

    result = await built.graph.ainvoke(initial_state(make_incident()))

    assert result["report"].outcome is Outcome.applied
    assert gate.calls == []          # RaisingGate/SpyGate never invoked
    assert built.applier.applied[0].action is ActionType.restart_service


async def test_needs_approval_waits_for_gate_then_applies():
    # patch_code mitigation (rule 3) -> HITL -> approve -> applied
    gate = SpyGate(HitlDecision(choice=HitlChoice.approve))
    patch_plan = [_plan(ActionType.patch_code, patch="+cap = 100\n",
                        target_file="mock_app/faults/memory_leak.py")]
    built = _graph(gate=gate, plan_queue=patch_plan, sandbox_results=[_result(True)])

    result = await built.graph.ainvoke(initial_state(make_incident()))

    assert result["report"].hitl_choice is HitlChoice.approve
    assert result["report"].outcome is Outcome.applied
    assert len(gate.calls) == 1


async def test_reflective_retry_switches_action():
    # verify fails for restart, second proposal is scale_out, verify passes
    gate = RaisingGate()
    plan_queue = [_plan(ActionType.restart_service), _plan(ActionType.scale_out, workers=4)]
    built = _graph(gate=gate, plan_queue=plan_queue,
                    sandbox_results=[_result(False), _result(True)])

    result = await built.graph.ainvoke(initial_state(make_incident()))

    assert result["report"].attempted_actions == ["restart_service", "scale_out"]
    assert result["report"].retries == 1
    assert result["report"].outcome is Outcome.applied


async def test_retries_exhausted_escalates():
    # verify always fails -> 3 attempts -> report escalated
    gate = RaisingGate()
    plan_queue = [_plan(), _plan(), _plan()]
    built = _graph(gate=gate, plan_queue=plan_queue, sandbox_results=[_result(False)])

    result = await built.graph.ainvoke(initial_state(make_incident()))

    assert result["report"].retries == 2
    assert result["report"].outcome is Outcome.escalated
    assert len(built.executor.calls) == 3


async def test_blocked_proposal_reports_blocked():
    # unknown-target mitigation -> policy block -> report, no apply, no hitl
    gate = RaisingGate()
    plan_queue = [RemediationPlan(mitigation=ProposedAction(
        action=ActionType.restart_service, reason="t", target="unknown_service"))]
    built = _graph(gate=gate, plan_queue=plan_queue, sandbox_results=[_result(True)])

    result = await built.graph.ainvoke(initial_state(make_incident()))

    assert result["report"].outcome is Outcome.blocked
    assert built.applier.applied == []
    assert gate.calls == []


async def test_expired_gate_escalates():
    # gate scripted to return HitlDecision(choice=expired, decided_by="ttl")
    gate = SpyGate(HitlDecision(choice=HitlChoice.expired, decided_by="ttl"))
    patch_plan = [_plan(ActionType.patch_code, patch="+cap = 100\n",
                        target_file="mock_app/faults/memory_leak.py")]
    built = _graph(gate=gate, plan_queue=patch_plan, sandbox_results=[_result(True)])

    result = await built.graph.ainvoke(initial_state(make_incident()))

    assert result["report"].outcome is Outcome.escalated
    assert result["report"].hitl_choice is HitlChoice.expired


async def test_promoted_run_enters_at_sandbox():
    # initial_state(..., plan=RemediationPlan(mitigation=patch_action), triage=make_triage())
    # triage/propose LLMs are fakes that RAISE if invoked
    gate = SpyGate(HitlDecision(choice=HitlChoice.approve))
    patch_action = ProposedAction(action=ActionType.patch_code, reason="durable fix",
                                  patch="+cap = 100\n", target_file="mock_app/faults/memory_leak.py")
    built = _graph(gate=gate, triage_llm=RaisingLLM(), propose_llm=RaisingLLM(),
                    sandbox_results=[_result(True)])

    result = await built.graph.ainvoke(initial_state(
        make_incident(), plan=RemediationPlan(mitigation=patch_action), triage=make_triage()))

    assert result["report"].mitigation_action is ActionType.patch_code
    assert result["report"].outcome is Outcome.applied


async def test_durable_fix_filed_on_applied_run():
    # plan carries a durable_fix; auto-applied -> registry.file called once
    gate = RaisingGate()
    durable_fix = ProposedAction(action=ActionType.patch_code, reason="cap growth",
                                 patch="+cap = 100\n", target_file="mock_app/faults/memory_leak.py")
    plan_queue = [RemediationPlan(
        mitigation=ProposedAction(action=ActionType.restart_service, reason="restart"),
        durable_fix=durable_fix,
    )]
    built = _graph(gate=gate, plan_queue=plan_queue, sandbox_results=[_result(True)])

    result = await built.graph.ainvoke(initial_state(make_incident()))

    assert result["report"].outcome is Outcome.applied
    assert len(built.registry.filed) == 1

"""Report node v2 tests: outcome derivation, durable-fix filing, report construction."""

import pytest

from agent.actions import ActionType, ProposedAction
from agent.nodes.report import report_node
from agent.state import (
    AttemptRecord,
    HitlChoice,
    HitlDecision,
    IncidentReport,
    NodeUsage,
    Outcome,
    PolicyDecision,
    PolicyVerdict,
    RemediationPlan,
    SandboxResult,
)
from tests.test_agent_nodes import make_incident, make_triage


class FakeSink:
    def __init__(self):
        self.reports: list[IncidentReport] = []

    def emit(self, report: IncidentReport) -> None:
        self.reports.append(report)


class FakeRegistry:
    def __init__(self):
        self.filed: list[dict] = []

    def file(self, *, incident, triage, fix) -> None:
        self.filed.append({"incident": incident, "triage": triage, "fix": fix})


def _patch_action() -> ProposedAction:
    return ProposedAction(
        action=ActionType.patch_code, reason="durable fix",
        patch="--- a\n+++ b\n", target_file="mock_app/main.py",
    )


def _attempt(action_name: str) -> AttemptRecord:
    extra = {}
    if action_name == "scale_out":
        extra["workers"] = 2
    elif action_name == "toggle_feature_flag":
        extra["flag_name"] = "new_ui"
    action = ProposedAction(action=ActionType(action_name), reason="attempt", **extra)
    result = SandboxResult(passed=False, exit_code=1, stdout="", stderr="", duration_ms=10.0)
    return AttemptRecord(action=action, result=result)


def _state(*, applied=False, apply_error=None, hitl=None, policy=None,
           durable_fix=None, attempted=None, mitigation=None):
    plan = RemediationPlan(
        mitigation=mitigation or ProposedAction(action=ActionType.restart_service, reason="restart"),
        durable_fix=durable_fix,
    )
    return {
        "incident": make_incident(),
        "triage": make_triage(),
        "retrieved_context": [],
        "plan": plan,
        "attempted": attempted or [],
        "retries": 0,
        "sandbox_result": SandboxResult(passed=True, exit_code=0, stdout="", stderr="", duration_ms=0),
        "policy_verdict": PolicyVerdict(decision=policy) if policy else None,
        "hitl_decision": HitlDecision(choice=hitl) if hitl else None,
        "applied": applied,
        "apply_error": apply_error,
        "report": None,
        "usage": [
            NodeUsage(node="triage", model="m", input_tokens=100, output_tokens=20, latency_ms=500),
            NodeUsage(node="propose", model="m", input_tokens=200, output_tokens=50, latency_ms=800),
        ],
    }


async def test_auto_applied_outcome_without_hitl():
    state = _state(applied=True, policy=PolicyDecision.allow)
    out = await report_node(state, sink=FakeSink(), registry=FakeRegistry())
    assert out["report"].outcome is Outcome.applied
    assert out["report"].hitl_choice is None
    assert out["report"].mitigation_action is ActionType.restart_service


async def test_expired_hitl_maps_to_escalated_and_keeps_choice():
    state = _state(hitl=HitlChoice.expired)
    report = (await report_node(state, sink=FakeSink(), registry=FakeRegistry()))["report"]
    assert report.outcome is Outcome.escalated
    assert report.hitl_choice is HitlChoice.expired


async def test_apply_failure_is_escalated_with_error():
    state = _state(applied=False, apply_error="docker down",
                   hitl=HitlChoice.approve, policy=PolicyDecision.needs_approval)
    report = (await report_node(state, sink=FakeSink(), registry=FakeRegistry()))["report"]
    assert report.outcome is Outcome.escalated
    assert report.apply_error == "docker down"


async def test_rejected_outcome():
    state = _state(applied=False, hitl=HitlChoice.reject, policy=PolicyDecision.allow)
    report = (await report_node(state, sink=FakeSink(), registry=FakeRegistry()))["report"]
    assert report.outcome is Outcome.rejected


async def test_blocked_outcome():
    state = _state(applied=False, policy=PolicyDecision.block)
    report = (await report_node(state, sink=FakeSink(), registry=FakeRegistry()))["report"]
    assert report.outcome is Outcome.blocked


async def test_durable_fix_filed_on_every_terminal_outcome():
    # §5: the durable fix lands in the "open durable fixes" queue regardless of
    # whether the mitigation was applied, rejected, or blocked.
    registry = FakeRegistry()
    state = _state(applied=True, durable_fix=_patch_action())
    await report_node(state, sink=FakeSink(), registry=registry)
    assert len(registry.filed) == 1

    registry = FakeRegistry()
    state = _state(applied=False, hitl=HitlChoice.reject, durable_fix=_patch_action())
    await report_node(state, sink=FakeSink(), registry=registry)
    assert len(registry.filed) == 1

    registry = FakeRegistry()
    state = _state(applied=False, policy=PolicyDecision.block, durable_fix=_patch_action())
    await report_node(state, sink=FakeSink(), registry=registry)
    assert len(registry.filed) == 1


async def test_no_durable_fix_files_nothing():
    registry = FakeRegistry()
    state = _state(applied=True, durable_fix=None)
    await report_node(state, sink=FakeSink(), registry=registry)
    assert registry.filed == []


async def test_attempted_action_trail_recorded():
    state = _state(applied=True, attempted=[_attempt("restart_service"),
                                             _attempt("scale_out")])
    report = (await report_node(state, sink=FakeSink(), registry=FakeRegistry()))["report"]
    assert report.attempted_actions == ["restart_service", "scale_out"]


async def test_report_totals_tokens_and_latency():
    sink = FakeSink()
    state = _state(applied=True, policy=PolicyDecision.allow)
    result = await report_node(state, sink=sink, registry=FakeRegistry())
    assert result["report"].total_input_tokens == 300
    assert result["report"].total_output_tokens == 70
    assert result["report"].total_latency_ms == 1300
    assert len(sink.reports) == 1


async def test_applied_target_file_set_only_for_patch_code():
    state = _state(applied=True, mitigation=_patch_action())
    report = (await report_node(state, sink=FakeSink(), registry=FakeRegistry()))["report"]
    assert report.applied_target_file == "mock_app/main.py"

    state2 = _state(applied=True)
    report2 = (await report_node(state2, sink=FakeSink(), registry=FakeRegistry()))["report"]
    assert report2.applied_target_file is None


async def test_report_persists_per_node_usage_for_cost_attribution():
    # Dollar cost needs per-node MODEL attribution: aggregate tokens alone cannot be
    # priced when nodes run on different models (Haiku vs Sonnet differ 3x).
    state = _state(applied=True)
    report = (await report_node(state, sink=FakeSink(), registry=FakeRegistry()))["report"]
    assert [u.node for u in report.node_usage] == ["triage", "propose"]
    assert [u.model for u in report.node_usage] == ["m", "m"]
    assert [u.input_tokens for u in report.node_usage] == [100, 200]
    assert [u.latency_ms for u in report.node_usage] == [500, 800]


async def test_report_node_usage_defaults_to_empty_when_no_usage_recorded():
    state = _state(applied=True)
    state["usage"] = []
    report = (await report_node(state, sink=FakeSink(), registry=FakeRegistry()))["report"]
    assert report.node_usage == []
    assert report.total_input_tokens == 0


async def test_persisted_node_usage_prices_each_node_at_its_own_model_rate():
    from observability.pricing import total_cost_usd

    state = _state(applied=True)
    state["usage"] = [
        NodeUsage(node="triage", model="claude-haiku-4-5-20251001",
                  input_tokens=1_000_000, output_tokens=0, latency_ms=1.0),
        NodeUsage(node="propose", model="claude-sonnet-4-6",
                  input_tokens=1_000_000, output_tokens=0, latency_ms=1.0),
    ]
    report = (await report_node(state, sink=FakeSink(), registry=FakeRegistry()))["report"]
    # 1M Haiku input @ $1/1M + 1M Sonnet input @ $3/1M = $4.00
    assert total_cost_usd(report.node_usage) == pytest.approx(4.00)

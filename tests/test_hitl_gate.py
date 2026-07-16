"""ApprovalGate: TTL expiry, evidence-brief building, resolve/list_pending (ADR-0005)."""

import asyncio

from agent.actions import ActionType, ProposedAction
from agent.state import (
    HitlChoice, HitlDecision, PolicyDecision, PolicyVerdict, RemediationPlan, SandboxResult,
)
from hitl.gate import ApprovalGate, build_brief
from tests.test_agent_nodes import make_incident, make_triage


def _verdict(**kwargs) -> PolicyVerdict:
    defaults = dict(decision=PolicyDecision.needs_approval, violated_rules=["irreversibility_gate"],
                    reasons=["patch_code always requires approval"])
    defaults.update(kwargs)
    return PolicyVerdict(**defaults)


def _passing_sandbox() -> SandboxResult:
    return SandboxResult(passed=True, exit_code=0, stdout="1 passed", stderr="", duration_ms=250.0,
                         before_metrics={"rss_mb": 200.0}, after_metrics={"rss_mb": 40.0})


def _plan_with_durable_fix() -> RemediationPlan:
    return RemediationPlan(
        mitigation=ProposedAction(action=ActionType.restart_service, reason="clear leak"),
        durable_fix=ProposedAction(action=ActionType.patch_code, reason="cap growth",
                                   patch="+cap = 100\n", target_file="mock_app/faults/memory_leak.py"),
    )


def _plan_plain() -> RemediationPlan:
    return RemediationPlan(mitigation=ProposedAction(action=ActionType.restart_service, reason="clear leak"))


async def _ask(gate: ApprovalGate, incident_id: str = "i-1") -> HitlDecision:
    incident = make_incident(incident_id=incident_id)
    return await gate.request_approval(
        incident=incident, plan=_plan_with_durable_fix(), sandbox=_passing_sandbox(),
        verdict=_verdict(), triage=make_triage())


async def test_approve_resolves_and_clears_brief():
    gate = ApprovalGate(ttl_s=5.0)
    task = asyncio.create_task(_ask(gate, incident_id="i-1"))
    await asyncio.sleep(0)  # let the future register
    assert gate.list_pending()[0]["incident_id"] == "i-1"
    gate.resolve("i-1", HitlChoice.approve)
    decision = await task
    assert decision.choice is HitlChoice.approve
    assert gate.list_pending() == []


async def test_ttl_expiry_returns_expired_decision():
    gate = ApprovalGate(ttl_s=0.05)
    decision = await _ask(gate, incident_id="i-2")
    assert decision.choice is HitlChoice.expired
    assert decision.decided_by == "ttl"
    assert gate.list_pending() == []


async def test_env_ttl_used_when_not_passed(monkeypatch):
    monkeypatch.setenv("HITL_TTL_S", "42")
    assert ApprovalGate().ttl_s == 42.0


def test_build_brief_carries_evidence_and_durable_fix():
    brief = build_brief(incident=make_incident(), plan=_plan_with_durable_fix(),
                        sandbox=_passing_sandbox(), verdict=_verdict(),
                        triage=make_triage(), expires_at=123.0)
    assert brief["evidence"]["before_metrics"] is not None
    assert brief["evidence"]["after_metrics"] is not None
    assert brief["policy"]["decision"] == "needs_approval"
    assert brief["durable_fix"]["action"] == "patch_code"
    assert brief["mitigation"]["action"] == "restart_service"


def test_build_brief_without_sandbox_or_fix():
    brief = build_brief(incident=make_incident(), plan=_plan_plain(),
                        sandbox=None, verdict=_verdict(), triage=None, expires_at=1.0)
    assert brief["evidence"] is None
    assert brief["durable_fix"] is None

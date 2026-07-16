"""DurableFixRegistry: file/list/promote roundtrip, refile guard, AutoApprover stub (ADR-0005)."""

from agent.actions import ActionType, ProposedAction
from agent.state import HitlChoice
from hitl.auto import AutoApprover
from hitl.registry import DurableFixRegistry
from tests.test_agent_nodes import make_incident, make_triage


def _patch_action() -> ProposedAction:
    return ProposedAction(action=ActionType.patch_code, reason="cap growth",
                          patch="+cap = 100\n", target_file="mock_app/faults/memory_leak.py")


async def test_file_list_promote_roundtrip():
    registry = DurableFixRegistry()
    registry.file(incident=make_incident(incident_id="i-1"), triage=make_triage(),
                  fix=_patch_action())
    assert registry.list_open()[0]["incident_id"] == "i-1"
    assert registry.promote("i-1") is True
    assert registry.list_open() == []
    entry = await registry.next_promotion()
    assert entry["incident"].incident_id == "i-1"
    assert entry["fix"].action is ActionType.patch_code


def test_promote_unknown_returns_false():
    assert DurableFixRegistry().promote("nope") is False


def test_refile_after_promote_is_ignored():
    registry = DurableFixRegistry()
    incident = make_incident(incident_id="i-1")
    registry.file(incident=incident, triage=None, fix=_patch_action())
    registry.promote("i-1")
    registry.file(incident=incident, triage=None, fix=_patch_action())
    assert registry.list_open() == []


async def test_auto_approver_always_approves():
    decision = await AutoApprover().request_approval(incident=None, plan=None,
                                                     sandbox=None, verdict=None, triage=None)
    assert decision.choice is HitlChoice.approve
    assert decision.decided_by == "auto-approver"

"""Sandbox node: verify() wiring, attempt tracking, skip logic (ADR-0002/0006)."""

import pytest

from agent.actions import ActionType, ProposedAction
from agent.nodes.sandbox import sandbox_node
from agent.state import AttemptRecord, RemediationPlan, SandboxResult, initial_state
from stream.schema import FaultKind
from tests.test_agent_nodes import make_incident


class FakeExecutor:
    def __init__(self, result):
        self._result = result
        self.calls = []

    async def verify(self, action, fault_kind):
        self.calls.append((action, fault_kind))
        return self._result


def _result(passed: bool) -> SandboxResult:
    return SandboxResult(passed=passed, exit_code=0 if passed else 1,
                         stdout="", stderr="", duration_ms=1.0)


def _state(action=ActionType.restart_service, **kwargs):
    state = initial_state(make_incident())
    state["plan"] = RemediationPlan(
        mitigation=ProposedAction(action=action, reason="test", **kwargs))
    return state


async def test_verify_called_with_mitigation_and_fault_kind():
    executor = FakeExecutor(_result(True))
    state = _state()
    out = await sandbox_node(state, executor=executor)
    action, kind = executor.calls[0]
    assert action.action is ActionType.restart_service
    assert kind is FaultKind.memory_leak
    assert out["sandbox_result"].passed is True


async def test_attempt_appended_and_retries_counted():
    executor = FakeExecutor(_result(False))
    state = _state()
    prior = AttemptRecord(
        action=ProposedAction(action=ActionType.restart_service, reason="1st"),
        result=_result(False))
    state["attempted"] = [prior]
    out = await sandbox_node(state, executor=executor)
    assert len(out["attempted"]) == 2
    assert out["retries"] == 1


async def test_escalate_skips_sandbox():
    executor = FakeExecutor(_result(True))
    state = _state(action=ActionType.escalate)
    out = await sandbox_node(state, executor=executor)
    assert out["sandbox_result"] is None
    assert executor.calls == []


async def test_non_reproducible_fault_skips_sandbox(monkeypatch):
    monkeypatch.setattr("agent.nodes.sandbox.is_reproducible", lambda kind: False)
    executor = FakeExecutor(_result(True))
    state = _state()
    out = await sandbox_node(state, executor=executor)
    assert out["sandbox_result"] is None
    assert executor.calls == []

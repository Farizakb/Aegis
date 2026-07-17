from agent.actions import ActionType, ProposedAction
from agent.nodes.apply import apply_node
from agent.state import PolicyDecision, PolicyVerdict, RemediationPlan, initial_state
from tests.test_agent_nodes import make_incident


class SpyApplier:
    def __init__(self, exc=None):
        self.applied = []
        self._exc = exc

    def apply(self, action):
        if self._exc:
            raise self._exc
        self.applied.append(action)


class SpyEngine:
    def __init__(self):
        self.recorded = []

    def record_apply(self, proposal, decision):
        self.recorded.append((proposal, decision))


def _state(decision=PolicyDecision.allow):
    state = initial_state(make_incident())
    state["plan"] = RemediationPlan(mitigation=ProposedAction(
        action=ActionType.restart_service, reason="leak"))
    state["policy_verdict"] = PolicyVerdict(decision=decision)
    return state


async def test_apply_success_records_and_flags_applied():
    applier, engine = SpyApplier(), SpyEngine()
    out = await apply_node(_state(), applier=applier, engine=engine)
    assert out == {"applied": True}
    assert len(applier.applied) == 1
    assert engine.recorded[0][1] is PolicyDecision.allow


async def test_apply_failure_is_caught_and_not_recorded():
    applier, engine = SpyApplier(exc=RuntimeError("docker down")), SpyEngine()
    out = await apply_node(_state(), applier=applier, engine=engine)
    assert out["applied"] is False
    assert "docker down" in out["apply_error"]
    assert engine.recorded == []

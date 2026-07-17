from agent.actions import ActionType, ProposedAction
from agent.nodes.policy import policy_node
from agent.state import PolicyDecision, PolicyVerdict, RemediationPlan, initial_state
from tests.test_agent_nodes import make_incident


async def test_policy_node_evaluates_the_typed_mitigation():
    class SpyEngine:
        def evaluate(self, *, proposal, sandbox, triage):
            self.seen = (proposal, sandbox, triage)
            return PolicyVerdict(decision=PolicyDecision.allow)

    engine = SpyEngine()
    state = initial_state(make_incident())
    state["plan"] = RemediationPlan(mitigation=ProposedAction(
        action=ActionType.restart_service, reason="leak"))

    out = await policy_node(state, engine=engine)

    assert isinstance(engine.seen[0], ProposedAction)
    assert out["policy_verdict"].decision is PolicyDecision.allow

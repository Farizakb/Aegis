from agent.nodes.policy import policy_node
from agent.state import PolicyDecision, ProposedFix
from policy.engine import PolicyEngine
from tests.test_agent_nodes import make_incident


async def test_policy_node_returns_verdict():
    engine = PolicyEngine()
    fix = ProposedFix(
        description="cap memory",
        patch="+cap = 100\n",
        target_file="mock_app/faults/memory_leak.py",
    )
    state = {
        "incident": make_incident(),
        "triage": None,
        "retrieved_context": [],
        "proposed_fix": fix,
        "retries": 0,
        "sandbox_result": None,
        "policy_verdict": None,
        "hitl_decision": None,
        "report": None,
        "usage": [],
    }
    result = await policy_node(state, engine)
    assert result["policy_verdict"].decision == PolicyDecision.allow

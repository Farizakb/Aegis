from agent.nodes.policy import policy_node
from agent.state import PolicyDecision, ProposedFix, SandboxResult, TriageResult
from policy.engine import PolicyEngine
from stream.schema import FaultKind
from tests.test_agent_nodes import make_incident


def _base_state(**overrides):
    state = {
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
    state.update(overrides)
    return state


async def test_policy_node_needs_approval_for_proven_patch():
    engine = PolicyEngine()
    fix = ProposedFix(
        description="cap memory",
        patch="+cap = 100\n",
        target_file="mock_app/faults/memory_leak.py",
    )
    sandbox = SandboxResult(passed=True, exit_code=0, stdout="1 passed", stderr="", duration_ms=100)
    triage = TriageResult(
        fault_kind=FaultKind.memory_leak,
        root_cause="unbounded list growth",
        confidence=0.9,
        reasoning="rss grows linearly",
    )
    state = _base_state(proposed_fix=fix, sandbox_result=sandbox, triage=triage)

    result = await policy_node(state, engine)

    # patch_code is irreversible/catalog-flagged, so even a fully proven
    # proposal is never auto-allowed — it must always clear HITL.
    assert result["policy_verdict"].decision == PolicyDecision.needs_approval
    assert "irreversibility_gate" in result["policy_verdict"].violated_rules


async def test_policy_node_blocks_malformed_fix():
    engine = PolicyEngine()
    fix = ProposedFix(
        description="cap memory",
        patch="+cap = 100\n",
        target_file=None,
    )
    state = _base_state(proposed_fix=fix)

    result = await policy_node(state, engine)

    assert result["policy_verdict"].decision == PolicyDecision.block
    assert "deny_by_default" in result["policy_verdict"].violated_rules

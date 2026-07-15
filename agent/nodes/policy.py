"""Policy node: gate the proposed fix against the deterministic rule engine."""

from __future__ import annotations

from agent.state import AgentState


async def policy_node(state: AgentState, engine) -> dict:
    fix = state["proposed_fix"]
    # Legacy bridge until Phase 4 rework: the graph still carries ProposedFix,
    # which is by definition a patch_code proposal. Dict form on purpose —
    # a malformed fix (e.g. missing target_file) must hit deny-by-default.
    proposal = {
        "action": "patch_code",
        "reason": fix.description if fix else "",
        "patch": fix.patch if fix else None,
        "target_file": fix.target_file if fix else None,
    }
    verdict = engine.evaluate(
        proposal=proposal,
        sandbox=state.get("sandbox_result"),
        triage=state.get("triage"),
    )
    return {"policy_verdict": verdict}

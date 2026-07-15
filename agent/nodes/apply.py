"""Apply node: write the proven patch to the live working tree."""

from __future__ import annotations

from agent.actions import ProposedAction
from agent.state import AgentState


async def apply_node(state: AgentState, applier, engine) -> dict:
    fix = state["proposed_fix"]
    applier.apply(target_file=fix.target_file, patch=fix.patch)
    # Same legacy-bridge proposal shape built in policy_node; by the time we
    # reach apply_node it has already passed engine.evaluate (non-block), so
    # it is guaranteed to parse into the typed catalog.
    proposal = ProposedAction(
        action="patch_code",
        reason=fix.description,
        patch=fix.patch,
        target_file=fix.target_file,
    )
    engine.record_apply(proposal, state["policy_verdict"].decision)
    return {}

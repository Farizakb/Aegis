"""Apply node: write the proven patch to the live working tree."""

from __future__ import annotations

from agent.state import AgentState


async def apply_node(state: AgentState, applier) -> dict:
    fix = state["proposed_fix"]
    applier.apply(target_file=fix.target_file, patch=fix.patch)
    return {}

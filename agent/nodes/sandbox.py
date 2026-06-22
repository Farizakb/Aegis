"""Sandbox node: run proposed patch in an isolated Docker container."""

from __future__ import annotations

from agent.state import AgentState


async def sandbox_node(state: AgentState, executor) -> dict:
    fix = state["proposed_fix"]
    result = await executor.run(patch=fix.patch, target_file=fix.target_file)
    return {"sandbox_result": result}

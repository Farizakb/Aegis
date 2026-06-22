"""HITL node: suspend the graph until a human approves or rejects."""

from __future__ import annotations

from agent.state import AgentState


async def hitl_node(state: AgentState, gate) -> dict:
    decision = await gate.request_approval(
        incident=state["incident"],
        fix=state["proposed_fix"],
        verdict=state["policy_verdict"],
    )
    return {"hitl_decision": decision}

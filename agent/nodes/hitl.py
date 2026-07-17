"""HITL node: suspend on an evidence brief until a human resolves or the TTL fires."""

from __future__ import annotations

from agent.state import AgentState


async def hitl_node(state: AgentState, gate) -> dict:
    decision = await gate.request_approval(
        incident=state["incident"],
        plan=state["plan"],
        sandbox=state.get("sandbox_result"),
        verdict=state["policy_verdict"],
        triage=state.get("triage"),
    )
    return {"hitl_decision": decision}

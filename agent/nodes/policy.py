"""Policy node: gate the proposed fix against deterministic rules."""

from __future__ import annotations

from agent.state import AgentState


async def policy_node(state: AgentState, engine) -> dict:
    verdict = engine.evaluate(
        fix=state["proposed_fix"],
        triage=state.get("triage"),
        incident=state.get("incident"),
    )
    return {"policy_verdict": verdict}

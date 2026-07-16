"""Policy node: gate the plan's mitigation against the deterministic rule engine."""

from __future__ import annotations

from agent.state import AgentState


async def policy_node(state: AgentState, engine) -> dict:
    verdict = engine.evaluate(
        proposal=state["plan"].mitigation,
        sandbox=state.get("sandbox_result"),
        triage=state.get("triage"),
    )
    return {"policy_verdict": verdict}

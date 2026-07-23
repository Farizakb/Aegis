"""HITL node: suspend on an evidence brief until a human resolves or the TTL fires."""

from __future__ import annotations

from opentelemetry import trace

from agent.state import AgentState
from observability.tracing import traced


@traced("hitl.wait")
async def hitl_node(state: AgentState, gate) -> dict:
    decision = await gate.request_approval(
        incident=state["incident"],
        plan=state["plan"],
        sandbox=state.get("sandbox_result"),
        verdict=state["policy_verdict"],
        triage=state.get("triage"),
    )
    trace.get_current_span().set_attribute("hitl.choice", decision.choice.value)
    return {"hitl_decision": decision}

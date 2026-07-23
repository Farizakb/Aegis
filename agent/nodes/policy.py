"""Policy node: gate the plan's mitigation against the deterministic rule engine."""

from __future__ import annotations

from opentelemetry import trace

from agent.state import AgentState
from observability.tracing import traced


@traced("graph.policy")
async def policy_node(state: AgentState, engine) -> dict:
    verdict = engine.evaluate(
        proposal=state["plan"].mitigation,
        sandbox=state.get("sandbox_result"),
        triage=state.get("triage"),
    )
    trace.get_current_span().set_attribute("policy.decision", verdict.decision.value)
    return {"policy_verdict": verdict}

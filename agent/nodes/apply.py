"""Apply node: execute the policy-cleared mitigation on the live target."""

from __future__ import annotations

from opentelemetry import trace

from agent.state import AgentState
from observability.tracing import traced


@traced("apply.execute")
async def apply_node(state: AgentState, applier, engine) -> dict:
    mitigation = state["plan"].mitigation
    try:
        applier.apply(mitigation)
    except Exception as exc:
        # A failed live apply is an escalation, not a crash: report it.
        trace.get_current_span().set_attribute("apply.outcome", "error")
        return {"applied": False, "apply_error": str(exc)}
    engine.record_apply(mitigation, state["policy_verdict"].decision)
    trace.get_current_span().set_attribute("apply.outcome", "applied")
    return {"applied": True}

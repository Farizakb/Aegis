"""Apply node: execute the policy-cleared mitigation on the live target."""

from __future__ import annotations

from agent.state import AgentState


async def apply_node(state: AgentState, applier, engine) -> dict:
    mitigation = state["plan"].mitigation
    try:
        applier.apply(mitigation)
    except Exception as exc:
        # A failed live apply is an escalation, not a crash: report it.
        return {"applied": False, "apply_error": str(exc)}
    engine.record_apply(mitigation, state["policy_verdict"].decision)
    return {"applied": True}

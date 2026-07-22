"""Sandbox node: empirical fault replay of the plan's mitigation (ADR-0002)."""

from __future__ import annotations

from opentelemetry import trace

from agent.actions import ActionType
from agent.state import AgentState, AttemptRecord
from observability.tracing import traced
from stream.schema import is_reproducible


@traced("graph.sandbox")
async def sandbox_node(state: AgentState, executor) -> dict:
    mitigation = state["plan"].mitigation
    if mitigation.action is ActionType.escalate or not is_reproducible(
        state["incident"].fault_kind
    ):
        # Nothing to verify (escalate executes nothing) or the fault class cannot
        # be replayed: policy rule 2 caps the run at needs_approval downstream.
        return {"sandbox_result": None}

    result = await executor.verify(mitigation, state["incident"].fault_kind)
    attempted = state["attempted"] + [AttemptRecord(action=mitigation, result=result)]
    trace.get_current_span().set_attribute("sandbox.passed", result.passed)
    return {"sandbox_result": result, "attempted": attempted,
            "retries": len(attempted) - 1}

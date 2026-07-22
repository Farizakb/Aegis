"""Propose node: emit a RemediationPlan — mitigate now, file the durable fix (ADR-0003/0006)."""

from __future__ import annotations

import time

from opentelemetry import trace

from agent.actions import ActionType, ProposedAction
from agent.state import AgentState, NodeUsage, RemediationPlan
from observability.tracing import traced

CHUNK_CHAR_LIMIT = 1200
MIN_RELEVANCE_SCORE = 0.3
STDOUT_TAIL_CHARS = 400

CATALOG_GUIDE = """\
Available actions — pick the mitigation from these; durable_fix is optional and, when present, \
should be a patch_code action that fixes the root cause:
- restart_service: restart the service process; clears in-process state (memory growth, stuck \
connections). No extra params.
- rollback: revert to the previously deployed version; right when a recent deploy introduced the \
fault. No extra params.
- toggle_feature_flag: turn a feature flag OFF (kill switch); right when the fault is tied to a \
flagged feature. Requires flag_name. Known flags: risky_feature.
- scale_out: raise the worker count (1-16); the answer to traffic surges — restarting during a \
surge makes it worse. Requires workers.
- patch_code: a durable code fix as a unified diff. Never a first mitigation — propose it as \
durable_fix. Requires patch and target_file.
- escalate: hand off to a human when no automated action fits.
Every action's target must be "mock_app"."""


def _attempts_block(state: AgentState) -> str:
    blocks = []
    for i, attempt in enumerate(state["attempted"], start=1):
        r = attempt.result
        blocks.append(
            f"Attempt {i}: {attempt.action.action.value} — FAILED empirical verification\n"
            f"  reason: {r.failure_reason or 'unknown'}\n"
            f"  before metrics: {r.before_metrics}\n"
            f"  after metrics: {r.after_metrics}\n"
            f"  output tail: {r.stdout[-STDOUT_TAIL_CHARS:]}"
        )
    return "\n".join(blocks)


def _prompt(state: AgentState) -> str:
    incident = state["incident"]
    triage = state["triage"]
    relevant = [c for c in state["retrieved_context"] if c.score >= MIN_RELEVANCE_SCORE]
    context_text = "\n\n".join(
        f"[{c.source} score={c.score:.2f}]\n{c.content[:CHUNK_CHAR_LIMIT]}" for c in relevant
    )
    parts = [
        "You are an SRE remediation assistant. Choose the cheapest reversible mitigation "
        "that clears the incident NOW, and optionally draft a durable fix for later.",
        CATALOG_GUIDE,
        f"Incident: {incident.title}\n{incident.summary}\nFault kind: {incident.fault_kind.value}",
        f"Triage root cause: {triage.root_cause}\nReasoning: {triage.reasoning}"
        if triage else "Triage unavailable.",
        f"Retrieved context:\n{context_text}" if context_text else "No retrieved context.",
    ]
    if state["attempted"]:
        parts.append(
            "Previous attempts FAILED sandbox fault-replay verification:\n"
            + _attempts_block(state)
            + "\nRefine the approach or SWITCH to a different action. "
              "Do not repeat a failed attempt unchanged."
        )
    return "\n\n".join(parts)


@traced("graph.propose")
async def propose_node(state: AgentState, llm) -> dict:
    structured = llm.with_structured_output(RemediationPlan, include_raw=True)
    start = time.monotonic()
    response = await structured.ainvoke(_prompt(state))
    latency_ms = (time.monotonic() - start) * 1000

    plan = response["parsed"]
    if plan is None:
        # Deterministic safe fallback: an unparseable proposal is handed to a human.
        error = response.get("parsing_error")
        plan = RemediationPlan(mitigation=ProposedAction(
            action=ActionType.escalate,
            reason=f"proposal did not parse into the action catalog: {error}"[:500],
        ))

    usage_metadata = getattr(response["raw"], "usage_metadata", None) or {}
    usage = NodeUsage(
        node="propose",
        model=getattr(llm, "model", "unknown"),
        input_tokens=usage_metadata.get("input_tokens", 0),
        output_tokens=usage_metadata.get("output_tokens", 0),
        latency_ms=latency_ms,
    )
    trace.get_current_span().set_attribute("propose.mitigation_action", plan.mitigation.action.value)
    return {"plan": plan, "usage": state["usage"] + [usage]}

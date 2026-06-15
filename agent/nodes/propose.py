"""Propose node: draft a candidate fix from the incident, triage, and retrieved context."""

from __future__ import annotations

import time

from agent.state import AgentState, NodeUsage, ProposedFix

CHUNK_CHAR_LIMIT = 1200
MIN_RELEVANCE_SCORE = 0.3


def _prompt(state: AgentState) -> str:
    incident = state["incident"]
    triage = state["triage"]

    relevant_chunks = [c for c in state["retrieved_context"] if c.score >= MIN_RELEVANCE_SCORE]
    context_text = "\n\n".join(
        f"[{c.source} score={c.score:.2f}]\n{c.content[:CHUNK_CHAR_LIMIT]}" for c in relevant_chunks
    )

    return (
        "You are an SRE remediation assistant. Given the incident, triage, and retrieved "
        "runbook/commit context below, propose a concrete fix as a unified diff or config "
        "change targeting the relevant file in this repository.\n\n"
        f"Incident: {incident.title}\n{incident.summary}\n\n"
        f"Triage root cause: {triage.root_cause}\nReasoning: {triage.reasoning}\n\n"
        f"Retrieved context:\n{context_text}\n\n"
        "Respond with a description, a unified diff patch, and the target file path."
    )


async def propose_node(state: AgentState, llm) -> dict:
    structured = llm.with_structured_output(ProposedFix, include_raw=True)
    start = time.monotonic()
    response = await structured.ainvoke(_prompt(state))
    latency_ms = (time.monotonic() - start) * 1000

    usage_metadata = getattr(response["raw"], "usage_metadata", None) or {}
    usage = NodeUsage(
        node="propose",
        model=getattr(llm, "model", "unknown"),
        input_tokens=usage_metadata.get("input_tokens", 0),
        output_tokens=usage_metadata.get("output_tokens", 0),
        latency_ms=latency_ms,
    )
    return {"proposed_fix": response["parsed"], "usage": state["usage"] + [usage]}

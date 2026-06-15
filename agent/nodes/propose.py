"""Propose node: draft a candidate fix from the incident, triage, and retrieved context."""

from __future__ import annotations

from agent.state import AgentState, ProposedFix

CONTEXT_CHAR_LIMIT = 4000


def _prompt(state: AgentState) -> str:
    incident = state["incident"]
    triage = state["triage"]

    context_text = "\n\n".join(
        f"[{c.source} score={c.score:.2f}]\n{c.content}" for c in state["retrieved_context"]
    )[:CONTEXT_CHAR_LIMIT]

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
    structured = llm.with_structured_output(ProposedFix)
    result = await structured.ainvoke(_prompt(state))
    return {"proposed_fix": result}

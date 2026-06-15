"""Retrieve node: fetch runbook/commit context relevant to the triaged incident."""

from __future__ import annotations

from agent.state import AgentState, RetrievedChunk

TOP_K = 3


async def retrieve_node(state: AgentState, search_tool) -> dict:
    incident = state["incident"]
    triage = state["triage"]
    query = f"{incident.fault_kind.value}: {triage.root_cause}" if triage else incident.summary

    raw = await search_tool.ainvoke({"query": query, "top_k": TOP_K})
    return {"retrieved_context": [RetrievedChunk(**chunk) for chunk in raw]}

"""Retrieve node: fetch runbook/commit context relevant to the triaged incident."""

from __future__ import annotations

import json

from agent.state import AgentState
from retrieval.models import RetrievedChunk

TOP_K = 3


def _as_chunk_dict(item):
    """Normalize a search result into a chunk dict.

    A live MCP tool returns each chunk as a text content block
    ({"type": "text", "text": "<json>"}); in-process/mocked callers pass the
    chunk dict directly. Handle both so the node works on either path.
    """
    if isinstance(item, dict) and item.get("type") == "text" and "source" not in item:
        return json.loads(item["text"])
    return item


async def retrieve_node(state: AgentState, search_tool) -> dict:
    incident = state["incident"]
    triage = state["triage"]
    query = f"{incident.fault_kind.value}: {triage.root_cause}" if triage else incident.summary

    raw = await search_tool.ainvoke({"query": query, "top_k": TOP_K})
    return {"retrieved_context": [RetrievedChunk(**_as_chunk_dict(chunk)) for chunk in raw]}

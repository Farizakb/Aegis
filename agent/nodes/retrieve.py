"""Retrieve node: fetch runbook/commit context relevant to the triaged incident."""

from __future__ import annotations

import json

from opentelemetry import trace

from agent.state import AgentState
from observability.tracing import get_tracer, traced
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


@traced("graph.retrieve")
async def retrieve_node(state: AgentState, search_tool) -> dict:
    incident = state["incident"]
    triage = state["triage"]
    query = f"{incident.fault_kind.value}: {triage.root_cause}" if triage else incident.summary

    with get_tracer().start_as_current_span("mcp.search_knowledge") as span:
        span.set_attribute("query", query)
        raw = await search_tool.ainvoke({"query": query, "top_k": TOP_K})
    chunks = [RetrievedChunk(**_as_chunk_dict(chunk)) for chunk in raw]
    trace.get_current_span().set_attribute("retrieve.chunks", len(chunks))
    return {"retrieved_context": chunks}

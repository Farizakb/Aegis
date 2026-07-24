# evals/drivers.py
"""Tier-2 offline driver: replay a fixture through triage -> retrieve -> propose
directly (no sandbox, no MCP subprocess) and return a scorable result dict."""
from __future__ import annotations

import asyncio

from agent.nodes.propose import propose_node
from agent.nodes.retrieve import retrieve_node
from agent.nodes.triage import triage_node
from agent.state import RemediationPlan, initial_state
from evals.fixtures import EvalCase
from retrieval.search import search


class DirectSearchAdapter:
    """Wraps a sync psycopg connection as an async search tool `retrieve_node` accepts."""

    def __init__(self, conn):
        self._conn = conn

    async def ainvoke(self, payload: dict) -> list[dict]:
        chunks = await asyncio.to_thread(search, self._conn, payload["query"], payload["top_k"])
        return [c.model_dump() for c in chunks]


async def drive_propose(case: EvalCase, *, triage_llm, propose_llm,
                        search_tool) -> tuple[dict, RemediationPlan]:
    """Shared core: run triage -> retrieve -> propose and return both the
    scorable tier-2 result dict AND the RemediationPlan (needed by tier 3,
    which must hand the real ProposedAction to the sandbox executor)."""
    state = initial_state(case.incident)

    state.update(await triage_node(state, llm=triage_llm))
    state.update(await retrieve_node(state, search_tool=search_tool))
    state.update(await propose_node(state, llm=propose_llm))

    triage = state["triage"]
    plan = state["plan"]

    seen = set()
    retrieved_sources = []
    for chunk in state["retrieved_context"]:
        if chunk.source not in seen:
            seen.add(chunk.source)
            retrieved_sources.append(chunk.source)

    durable_fix = plan.durable_fix.action.value if plan.durable_fix else None

    result = {
        "id": case.id,
        "fault_kind_expected": case.truth["fault_kind"],
        "fault_kind_actual": triage.fault_kind.value if triage else None,
        "confidence": triage.confidence if triage else 0.0,
        "root_cause_actual": triage.root_cause if triage else "",
        "mitigation_actual": plan.mitigation.action.value,
        "durable_fix_actual": durable_fix,
        "durable_fix_target_file": plan.durable_fix.target_file if plan.durable_fix else None,
        "retrieved_sources": retrieved_sources,
        "acceptable_mitigations": case.truth["acceptable_mitigations"],
        "relevant_docs": case.truth["relevant_docs"],
        "root_cause_reference": case.truth["root_cause_reference"],
    }
    return result, plan


async def run_propose(case: EvalCase, *, triage_llm, propose_llm, search_tool) -> dict:
    result, _plan = await drive_propose(case, triage_llm=triage_llm, propose_llm=propose_llm,
                                        search_tool=search_tool)
    return result

"""LangGraph wiring for the Week 2 happy path: Triage -> Retrieve -> Propose."""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent.nodes.propose import propose_node
from agent.nodes.retrieve import retrieve_node
from agent.nodes.triage import triage_node
from agent.state import AgentState


def build_graph(triage_llm, propose_llm, search_tool) -> CompiledStateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("triage", partial(triage_node, llm=triage_llm))
    graph.add_node("retrieve", partial(retrieve_node, search_tool=search_tool))
    graph.add_node("propose", partial(propose_node, llm=propose_llm))

    graph.add_edge(START, "triage")
    graph.add_edge("triage", "retrieve")
    graph.add_edge("retrieve", "propose")
    graph.add_edge("propose", END)

    return graph.compile()

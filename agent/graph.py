"""LangGraph state machine: Triage -> Retrieve -> Propose -> Sandbox -> Policy -> HITL -> Apply -> Report."""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent.actions import ActionType
from agent.nodes.apply import apply_node
from agent.nodes.hitl import hitl_node
from agent.nodes.policy import policy_node
from agent.nodes.propose import propose_node
from agent.nodes.report import report_node
from agent.nodes.retrieve import retrieve_node
from agent.nodes.sandbox import sandbox_node
from agent.nodes.triage import triage_node
from agent.state import AgentState, HitlChoice, PolicyDecision

MAX_RETRIES = 2


def route_after_sandbox(state: AgentState) -> str:
    result = state["sandbox_result"]
    if result is None or result.passed:
        return "policy"
    if state["retries"] < MAX_RETRIES:
        return "propose"
    return "report"


def route_after_policy(state: AgentState) -> str:
    if state["policy_verdict"].decision == PolicyDecision.block:
        return "report"
    if state["plan"].mitigation.action is ActionType.escalate:
        return "report"  # terminal hand-off; outcome=escalated
    if state["policy_verdict"].decision == PolicyDecision.allow:
        return "apply"  # auto-apply: this is what rule 6's circuit meters
    return "hitl"


def route_after_hitl(state: AgentState) -> str:
    if state["hitl_decision"].choice == HitlChoice.approve:
        return "apply"
    return "report"


def route_entry(state: AgentState) -> str:
    # Promoted durable-fix runs arrive with `plan` preset: skip straight to
    # empirical verification; everything else starts at triage.
    return "sandbox" if state.get("plan") else "triage"


def build_graph(
    triage_llm,
    propose_llm,
    search_tool,
    executor,
    policy_engine,
    approval_gate,
    applier,
    report_sink,
    registry,
) -> CompiledStateGraph:
    g = StateGraph(AgentState)
    g.add_node("triage", partial(triage_node, llm=triage_llm))
    g.add_node("retrieve", partial(retrieve_node, search_tool=search_tool))
    g.add_node("propose", partial(propose_node, llm=propose_llm))
    g.add_node("sandbox", partial(sandbox_node, executor=executor))
    g.add_node("policy", partial(policy_node, engine=policy_engine))
    g.add_node("hitl", partial(hitl_node, gate=approval_gate))
    g.add_node("apply", partial(apply_node, applier=applier, engine=policy_engine))
    g.add_node("report", partial(report_node, sink=report_sink, registry=registry))

    g.add_conditional_edges(START, route_entry, {"triage": "triage", "sandbox": "sandbox"})
    g.add_edge("triage", "retrieve")
    g.add_edge("retrieve", "propose")
    g.add_edge("propose", "sandbox")

    g.add_conditional_edges("sandbox", route_after_sandbox, {
        "propose": "propose",
        "policy": "policy",
        "report": "report",
    })
    g.add_conditional_edges("policy", route_after_policy, {
        "hitl": "hitl",
        "apply": "apply",
        "report": "report",
    })
    g.add_conditional_edges("hitl", route_after_hitl, {
        "apply": "apply",
        "report": "report",
    })

    g.add_edge("apply", "report")
    g.add_edge("report", END)
    return g.compile()

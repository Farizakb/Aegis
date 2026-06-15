from datetime import datetime, timezone

from agent.graph import build_graph
from agent.state import ProposedFix, TriageResult
from stream.schema import FaultKind, IncidentEvent, Severity
from tests.test_agent_nodes import FakeLLM, FakeSearchTool


def make_incident() -> IncidentEvent:
    now = datetime.now(timezone.utc)
    return IncidentEvent(
        incident_id="inc-1",
        fault_kind=FaultKind.memory_leak,
        severity=Severity.critical,
        source="mock_app",
        title="memory_leak on mock_app",
        summary="rss climbed to 200MB",
        first_seen=now,
        last_seen=now,
        event_count=3,
        duplicate_count=5,
        sample_events=[],
        correlation_window_s=3.0,
    )


async def test_graph_runs_triage_then_retrieve_then_propose():
    triage_result = TriageResult(
        fault_kind=FaultKind.memory_leak,
        root_cause="unbounded list growth in MemoryLeakFault",
        confidence=0.9,
        reasoning="rss grows linearly with each tick",
    )
    proposed_fix = ProposedFix(
        description="cap memory growth",
        patch="--- a/mock_app/faults/memory_leak.py\n+++ b/mock_app/faults/memory_leak.py\n",
        target_file="mock_app/faults/memory_leak.py",
    )
    llm = FakeLLM({TriageResult: triage_result, ProposedFix: proposed_fix})
    search_tool = FakeSearchTool([{"source": "runbook:memory_leak.md", "content": "cap the chunk list", "score": 0.8}])

    graph = build_graph(llm, llm, search_tool)
    result = await graph.ainvoke(
        {
            "incident": make_incident(),
            "triage": None,
            "retrieved_context": [],
            "proposed_fix": None,
            "retries": 0,
            "sandbox_result": None,
            "policy_verdict": None,
            "hitl_decision": None,
            "usage": [],
        }
    )

    assert result["triage"] == triage_result
    assert result["retrieved_context"][0].source == "runbook:memory_leak.md"
    assert result["proposed_fix"] == proposed_fix
    assert [u.node for u in result["usage"]] == ["triage", "propose"]

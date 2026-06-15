from datetime import datetime, timezone

from agent.nodes.propose import propose_node
from agent.nodes.retrieve import retrieve_node
from agent.nodes.triage import triage_node
from agent.state import ProposedFix, RetrievedChunk, TriageResult
from stream.schema import FaultKind, IncidentEvent, RawEvent, Severity


def make_incident() -> IncidentEvent:
    now = datetime.now(timezone.utc)
    sample = RawEvent(
        event_id="evt-1",
        fault_kind=FaultKind.memory_leak,
        severity=Severity.critical,
        source="mock_app",
        message="rss high: 200MB",
        metric=200.0,
        ts=now,
        dedup_key="dk1",
    )
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
        sample_events=[sample],
        correlation_window_s=3.0,
    )


class FakeStructuredLLM:
    def __init__(self, result):
        self._result = result
        self.last_prompt = None

    async def ainvoke(self, prompt):
        self.last_prompt = prompt
        return self._result


class FakeLLM:
    def __init__(self, results: dict[type, object]):
        self._results = results
        self.structured: dict[type, FakeStructuredLLM] = {}

    def with_structured_output(self, model):
        structured = FakeStructuredLLM(self._results[model])
        self.structured[model] = structured
        return structured


class FakeSearchTool:
    def __init__(self, chunks: list[dict]):
        self._chunks = chunks
        self.last_args = None

    async def ainvoke(self, args):
        self.last_args = args
        return self._chunks


async def test_triage_node_returns_triage_result():
    triage_result = TriageResult(
        fault_kind=FaultKind.memory_leak,
        root_cause="unbounded list growth in MemoryLeakFault",
        confidence=0.9,
        reasoning="rss grows linearly with each tick",
    )
    llm = FakeLLM({TriageResult: triage_result})
    state = {"incident": make_incident(), "triage": None, "retrieved_context": [], "proposed_fix": None}

    result = await triage_node(state, llm)

    assert result["triage"] is triage_result
    assert "memory_leak" in llm.structured[TriageResult].last_prompt


async def test_retrieve_node_uses_triage_root_cause_in_query():
    triage_result = TriageResult(
        fault_kind=FaultKind.memory_leak,
        root_cause="unbounded list growth",
        confidence=0.9,
        reasoning="...",
    )
    search_tool = FakeSearchTool([{"source": "runbook:memory_leak.md", "content": "...", "score": 0.8}])
    state = {"incident": make_incident(), "triage": triage_result, "retrieved_context": [], "proposed_fix": None}

    result = await retrieve_node(state, search_tool)

    assert result["retrieved_context"] == [RetrievedChunk(source="runbook:memory_leak.md", content="...", score=0.8)]
    assert "unbounded list growth" in search_tool.last_args["query"]
    assert "memory_leak" in search_tool.last_args["query"]


async def test_propose_node_returns_proposed_fix():
    proposed_fix = ProposedFix(
        description="cap memory growth",
        patch="--- a/mock_app/faults/memory_leak.py\n+++ b/mock_app/faults/memory_leak.py\n",
        target_file="mock_app/faults/memory_leak.py",
    )
    llm = FakeLLM({ProposedFix: proposed_fix})
    triage_result = TriageResult(
        fault_kind=FaultKind.memory_leak,
        root_cause="unbounded list growth",
        confidence=0.9,
        reasoning="...",
    )
    retrieved = [RetrievedChunk(source="runbook:memory_leak.md", content="cap the chunk list", score=0.8)]
    state = {"incident": make_incident(), "triage": triage_result, "retrieved_context": retrieved, "proposed_fix": None}

    result = await propose_node(state, llm)

    assert result["proposed_fix"] is proposed_fix
    prompt = llm.structured[ProposedFix].last_prompt
    assert "unbounded list growth" in prompt
    assert "cap the chunk list" in prompt

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


class FakeRaw:
    def __init__(self, usage_metadata: dict):
        self.usage_metadata = usage_metadata


class FakeStructuredLLM:
    def __init__(self, result, usage_metadata: dict | None = None):
        self._result = result
        self._usage_metadata = usage_metadata or {"input_tokens": 100, "output_tokens": 20}
        self.last_prompt = None

    async def ainvoke(self, prompt):
        self.last_prompt = prompt
        return {"raw": FakeRaw(self._usage_metadata), "parsed": self._result}


class FakeLLM:
    def __init__(self, results: dict[type, object], model: str = "fake-model"):
        self._results = results
        self.model = model
        self.structured: dict[type, FakeStructuredLLM] = {}

    def with_structured_output(self, model, include_raw=False):
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
    llm = FakeLLM({TriageResult: triage_result}, model="claude-haiku-4-5-20251001")
    state = {
        "incident": make_incident(),
        "triage": None,
        "retrieved_context": [],
        "proposed_fix": None,
        "retries": 0,
        "sandbox_result": None,
        "policy_verdict": None,
        "hitl_decision": None,
        "report": None,
        "usage": [],
    }

    result = await triage_node(state, llm)

    assert result["triage"] == triage_result
    assert "memory_leak" in llm.structured[TriageResult].last_prompt
    assert len(result["usage"]) == 1
    usage = result["usage"][0]
    assert usage.node == "triage"
    assert usage.model == "claude-haiku-4-5-20251001"
    assert usage.input_tokens == 100
    assert usage.output_tokens == 20


async def test_retrieve_node_uses_triage_root_cause_in_query():
    triage_result = TriageResult(
        fault_kind=FaultKind.memory_leak,
        root_cause="unbounded list growth",
        confidence=0.9,
        reasoning="...",
    )
    search_tool = FakeSearchTool([{"source": "runbook:memory_leak.md", "content": "...", "score": 0.8}])
    state = {
        "incident": make_incident(),
        "triage": triage_result,
        "retrieved_context": [],
        "proposed_fix": None,
        "retries": 0,
        "sandbox_result": None,
        "policy_verdict": None,
        "hitl_decision": None,
        "report": None,
        "usage": [],
    }

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
    llm = FakeLLM({ProposedFix: proposed_fix}, model="claude-sonnet-4-6")
    triage_result = TriageResult(
        fault_kind=FaultKind.memory_leak,
        root_cause="unbounded list growth",
        confidence=0.9,
        reasoning="...",
    )
    retrieved = [RetrievedChunk(source="runbook:memory_leak.md", content="cap the chunk list", score=0.8)]
    state = {
        "incident": make_incident(),
        "triage": triage_result,
        "retrieved_context": retrieved,
        "proposed_fix": None,
        "retries": 0,
        "sandbox_result": None,
        "policy_verdict": None,
        "hitl_decision": None,
        "report": None,
        "usage": [],
    }

    result = await propose_node(state, llm)

    assert result["proposed_fix"] == proposed_fix
    prompt = llm.structured[ProposedFix].last_prompt
    assert "unbounded list growth" in prompt
    assert "cap the chunk list" in prompt
    assert len(result["usage"]) == 1
    usage = result["usage"][0]
    assert usage.node == "propose"
    assert usage.model == "claude-sonnet-4-6"

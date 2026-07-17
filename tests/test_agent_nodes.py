from datetime import datetime, timezone
from types import SimpleNamespace

from agent.actions import ActionType, ProposedAction
from agent.nodes.propose import propose_node
from agent.nodes.retrieve import retrieve_node
from agent.nodes.triage import triage_node
from agent.state import (
    AttemptRecord, RemediationPlan, RetrievedChunk, SandboxResult, TriageResult, initial_state,
)
from stream.schema import FaultKind, IncidentEvent, RawEvent, Severity


def make_incident(**overrides) -> IncidentEvent:
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
    defaults = dict(
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
    defaults.update(overrides)
    return IncidentEvent(**defaults)


def make_triage(**kwargs) -> TriageResult:
    defaults = dict(
        fault_kind=FaultKind.memory_leak,
        root_cause="unbounded list growth",
        confidence=0.9,
        reasoning="rss grows linearly with each tick",
    )
    defaults.update(kwargs)
    return TriageResult(**defaults)


class FakeRaw:
    def __init__(self, usage_metadata: dict):
        self.usage_metadata = usage_metadata


class _FakeStructuredResult:
    """Backs FakeLLM.with_structured_output; keyed-by-type fake used by triage/retrieve tests."""

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
        self.structured: dict[type, _FakeStructuredResult] = {}

    def with_structured_output(self, model, include_raw=False):
        structured = _FakeStructuredResult(self._results[model])
        self.structured[model] = structured
        return structured


class FakeSearchTool:
    def __init__(self, chunks: list[dict]):
        self._chunks = chunks
        self.last_args = None

    async def ainvoke(self, args):
        self.last_args = args
        return self._chunks


class FakeStructuredLLM:
    """Captures prompts; returns queued {'raw','parsed'} responses."""

    def __init__(self, parsed_queue):
        self.prompts = []
        self._queue = list(parsed_queue)
        self.model = "fake-model"

    def with_structured_output(self, schema, include_raw=True):
        return self

    async def ainvoke(self, prompt):
        self.prompts.append(prompt)
        return {"raw": SimpleNamespace(usage_metadata={"input_tokens": 10, "output_tokens": 5}),
                "parsed": self._queue.pop(0)}


def _plan(action=ActionType.restart_service, **kwargs) -> RemediationPlan:
    return RemediationPlan(mitigation=ProposedAction(action=action, reason="test", **kwargs))


async def test_triage_node_returns_triage_result():
    triage_result = make_triage(root_cause="unbounded list growth in MemoryLeakFault")
    llm = FakeLLM({TriageResult: triage_result}, model="claude-haiku-4-5-20251001")
    state = initial_state(make_incident())

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
    triage_result = make_triage(reasoning="...")
    search_tool = FakeSearchTool([{"source": "runbook:memory_leak.md", "content": "...", "score": 0.8}])
    state = initial_state(make_incident(), triage=triage_result)

    result = await retrieve_node(state, search_tool)

    assert result["retrieved_context"] == [RetrievedChunk(source="runbook:memory_leak.md", content="...", score=0.8)]
    assert "unbounded list growth" in search_tool.last_args["query"]
    assert "memory_leak" in search_tool.last_args["query"]


async def test_propose_returns_plan_and_usage():
    llm = FakeStructuredLLM([_plan()])
    state = initial_state(make_incident())
    state["triage"] = make_triage()
    out = await propose_node(state, llm=llm)
    assert isinstance(out["plan"], RemediationPlan)
    assert out["usage"][-1].node == "propose"
    assert "retries" not in out


async def test_first_attempt_prompt_lists_the_action_catalog():
    llm = FakeStructuredLLM([_plan()])
    state = initial_state(make_incident())
    state["triage"] = make_triage()
    await propose_node(state, llm=llm)
    prompt = llm.prompts[0]
    for name in ("restart_service", "rollback", "toggle_feature_flag",
                 "scale_out", "patch_code", "escalate"):
        assert name in prompt


async def test_retry_prompt_carries_failure_evidence_and_switch_instruction():
    llm = FakeStructuredLLM([_plan(ActionType.scale_out, workers=4)])
    state = initial_state(make_incident())
    state["triage"] = make_triage()
    failed = SandboxResult(passed=False, exit_code=1, stdout="latency stayed high",
                           stderr="", duration_ms=900.0,
                           before_metrics={"latency_ms": 200.0},
                           after_metrics={"latency_ms": 200.0},
                           failure_reason="latency_ms 200.0 above recovery ceiling 100.0")
    state["attempted"] = [AttemptRecord(
        action=ProposedAction(action=ActionType.restart_service, reason="try restart"),
        result=failed)]
    await propose_node(state, llm=llm)
    prompt = llm.prompts[0]
    assert "restart_service" in prompt and "FAILED" in prompt
    assert "above recovery ceiling" in prompt
    assert "SWITCH" in prompt


async def test_unparseable_proposal_falls_back_to_escalate():
    llm = FakeStructuredLLM([None])  # structured output failed to parse
    state = initial_state(make_incident())
    state["triage"] = make_triage()
    out = await propose_node(state, llm=llm)
    assert out["plan"].mitigation.action is ActionType.escalate

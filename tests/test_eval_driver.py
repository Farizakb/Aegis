# tests/test_eval_driver.py
import pytest

from agent.actions import ActionType, ProposedAction
from agent.state import RemediationPlan, TriageResult
from evals.drivers import run_propose
from evals.fixtures import EvalCase
from stream.schema import FaultKind, IncidentEvent, RawEvent, Severity


class _FakeStructured:
    def __init__(self, parsed):
        self._parsed = parsed

    async def ainvoke(self, _prompt):
        class _Raw:
            usage_metadata = {"input_tokens": 10, "output_tokens": 5}
        return {"parsed": self._parsed, "raw": _Raw(), "parsing_error": None}


class _FakeLLM:
    model = "fake"

    def __init__(self, parsed):
        self._parsed = parsed

    def with_structured_output(self, _schema, include_raw=False):
        return _FakeStructured(self._parsed)


class _FakeSearch:
    async def ainvoke(self, _payload):
        return [{"source": "runbook:memory_leak.md", "content": "restart clears it", "score": 0.9}]


def _case():
    t0 = "2026-07-23T12:00:00Z"
    inc = IncidentEvent(incident_id="memory_leak_01", fault_kind=FaultKind.memory_leak,
                        severity=Severity.critical, source="mock_app", title="t", summary="s",
                        first_seen=t0, last_seen=t0, event_count=1, duplicate_count=0,
                        sample_events=[RawEvent(event_id="e1", fault_kind=FaultKind.memory_leak,
                                                severity=Severity.critical, source="mock_app",
                                                message="rss high", metric=800.0, ts=t0, dedup_key="k")],
                        correlation_window_s=30.0)
    return EvalCase(id="memory_leak_01", incident=inc, truth={
        "fault_kind": "memory_leak", "acceptable_mitigations": ["restart_service"],
        "expected_durable_fix": "patch_code", "relevant_docs": ["runbook:memory_leak.md"],
        "root_cause_reference": "buffer growth", "retrieval_root_cause": "buffer growth"})


async def test_driver_returns_scorable_result():
    triage = TriageResult(fault_kind=FaultKind.memory_leak, root_cause="leak",
                          confidence=0.82, reasoning="rss climbs")
    plan = RemediationPlan(mitigation=ProposedAction(action=ActionType.restart_service, reason="clear"),
                           durable_fix=ProposedAction(action=ActionType.patch_code, reason="fix",
                                                      patch="x", target_file="mock_app/faults/mem.py"))
    r = await run_propose(_case(), triage_llm=_FakeLLM(triage), propose_llm=_FakeLLM(plan),
                          search_tool=_FakeSearch())
    assert r["fault_kind_actual"] == "memory_leak"
    assert r["confidence"] == 0.82
    assert r["mitigation_actual"] == "restart_service"
    assert r["durable_fix_actual"] == "patch_code"
    assert r["retrieved_sources"] == ["runbook:memory_leak.md"]
    assert r["acceptable_mitigations"] == ["restart_service"]

# tests/test_model_tiering.py
"""Fast, offline test for the model-tiering experiment runner. No network, no
Postgres — LLMs and the search tool are faked, mirroring tests/test_eval_driver.py."""
import evals.experiments.model_tiering as model_tiering
from agent.actions import ActionType, ProposedAction
from agent.state import RemediationPlan, TriageResult
from evals.fixtures import EvalCase
from stream.schema import FaultKind, IncidentEvent, RawEvent, Severity

TRIAGE = TriageResult(fault_kind=FaultKind.memory_leak, root_cause="leak",
                      confidence=0.82, reasoning="rss climbs")
PLAN = RemediationPlan(mitigation=ProposedAction(action=ActionType.restart_service, reason="clear"),
                       durable_fix=ProposedAction(action=ActionType.patch_code, reason="fix",
                                                  patch="x", target_file="mock_app/faults/mem.py"))


class _FakeStructured:
    def __init__(self, parsed):
        self._parsed = parsed

    async def ainvoke(self, _prompt):
        class _Raw:
            usage_metadata = {"input_tokens": 100, "output_tokens": 50}
        return {"parsed": self._parsed, "raw": _Raw(), "parsing_error": None}


class _FakeLLM:
    """Same real pricing-table model string used for both roles (triage and
    propose expect different schemas), so the fake dispatches on the
    requested schema rather than being fixed to one role at construction."""

    def __init__(self, model: str):
        self.model = model

    def with_structured_output(self, schema, include_raw=False):
        if schema is TriageResult:
            return _FakeStructured(TRIAGE)
        return _FakeStructured(PLAN)


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


async def test_run_experiment_sonnet_triage_costs_more_than_haiku(monkeypatch):
    fakes_by_model = {
        "claude-haiku-4-5-20251001": _FakeLLM("claude-haiku-4-5-20251001"),
        "claude-sonnet-4-6": _FakeLLM("claude-sonnet-4-6"),
    }

    def fake_get_llm(model: str):
        return fakes_by_model[model]

    monkeypatch.setattr(model_tiering, "get_llm", fake_get_llm)

    configs = [
        {"label": "triage-sonnet", "triage_model": "claude-sonnet-4-6",
         "propose_model": "claude-sonnet-4-6"},
        {"label": "triage-haiku", "triage_model": "claude-haiku-4-5-20251001",
         "propose_model": "claude-sonnet-4-6"},
    ]

    result = await model_tiering.run_experiment([_case()], configs, search_tool=_FakeSearch())

    assert result["configs"][0]["label"] == "triage-sonnet"
    assert result["configs"][1]["label"] == "triage-haiku"
    by_label = {c["label"]: c["metrics"] for c in result["configs"]}
    for label in ("triage-haiku", "triage-sonnet"):
        assert "triage_accuracy" in by_label[label]
        assert "action_selection_accuracy" in by_label[label]
        assert "total_cost_usd" in by_label[label]
        assert "mean_latency_ms" in by_label[label]
        assert by_label[label]["triage_accuracy"] == 1.0

    # Same token counts (100 in / 50 out) for every call in both configs; only
    # the triage-slot model's price differs (propose is Sonnet in both) ->
    # triage-sonnet must cost strictly more than triage-haiku.
    assert by_label["triage-sonnet"]["total_cost_usd"] > by_label["triage-haiku"]["total_cost_usd"]

    # comparison: baseline = triage-sonnet (configs[0]), candidate = triage-haiku
    # (configs[1]) -> haiku is cheaper, so cost_delta_usd must be negative.
    comparison = result["comparison"]
    assert comparison is not None
    assert comparison["baseline"] == "triage-sonnet"
    assert comparison["candidate"] == "triage-haiku"
    assert "cost_delta_usd" in comparison
    assert "triage_accuracy_delta" in comparison
    assert comparison["cost_delta_usd"] < 0


async def test_run_experiment_stores_per_case_results_and_sweep_comparisons(monkeypatch):
    """Each config keeps a diagnosable per-case `results` list, and `comparisons`
    holds every non-baseline config measured against configs[0] (the sweep view)."""
    monkeypatch.setattr(model_tiering, "get_llm", lambda m: _FakeLLM(m))

    configs = [
        {"label": "all-sonnet", "triage_model": "claude-sonnet-4-6",
         "propose_model": "claude-sonnet-4-6"},
        {"label": "triage-haiku", "triage_model": "claude-haiku-4-5-20251001",
         "propose_model": "claude-sonnet-4-6"},
        {"label": "all-haiku", "triage_model": "claude-haiku-4-5-20251001",
         "propose_model": "claude-haiku-4-5-20251001"},
    ]
    result = await model_tiering.run_experiment([_case()], configs, search_tool=_FakeSearch())

    for cfg in result["configs"]:
        assert len(cfg["results"]) == 1
        rec = cfg["results"][0]
        assert rec["id"] == "memory_leak_01"
        assert set(rec) >= {"mitigation_actual", "acceptable_mitigations", "action_correct"}
        # Fake plan mitigates with restart_service, which is in acceptable_mitigations.
        assert rec["action_correct"] is True

    # comparisons: every config after configs[0], each measured vs the baseline.
    assert [c["candidate"] for c in result["comparisons"]] == ["triage-haiku", "all-haiku"]
    assert all(c["baseline"] == "all-sonnet" for c in result["comparisons"])

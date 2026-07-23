import json

from evals.run_evals import _tier2_metrics, _write_result


def test_tier2_metrics_shape():
    results = [
        {"fault_kind_expected": "memory_leak", "fault_kind_actual": "memory_leak",
         "confidence": 0.8, "mitigation_actual": "restart_service",
         "acceptable_mitigations": ["restart_service"],
         "retrieved_sources": ["runbook:memory_leak.md"], "relevant_docs": ["runbook:memory_leak.md"],
         "geval_score": 0.7},
    ]
    m = _tier2_metrics(results)
    assert m["triage_accuracy"] == 1.0
    assert m["action_selection_accuracy"] == 1.0
    assert m["retrieval_recall_at_3"] == 1.0
    assert "mean_confidence" in m and "pct_below_confidence_floor" in m


def test_write_result_envelope(tmp_path):
    p = _write_result(tmp_path, 2, results=[], metrics={"triage_accuracy": 1.0},
                      model_config={"triage": "claude-haiku-4-5-20251001"})
    payload = json.loads(p.read_text(encoding="utf-8"))
    assert payload["tier"] == 2
    assert "code_git_sha" in payload and "git_sha" not in payload
    assert payload["model_config"]["triage"].startswith("claude")
    assert p.read_text(encoding="utf-8").endswith("\n")

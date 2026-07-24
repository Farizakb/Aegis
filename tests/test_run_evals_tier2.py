import argparse
import json

from evals.run_evals import (
    _missing_runbooks,
    _representative_cases,
    _run_tier2_cli,
    _tier2_metrics,
    _write_result,
)


def test_tier2_metrics_shape():
    results = [
        {"fault_kind_expected": "memory_leak", "fault_kind_actual": "memory_leak",
         "confidence": 0.8, "mitigation_actual": "restart_service",
         "acceptable_mitigations": ["restart_service"],
         "retrieved_sources": ["runbook:memory_leak.md"], "relevant_docs": ["runbook:memory_leak.md"],
         "geval_score": 0.7, "durable_fix_target_file": None},
    ]
    m = _tier2_metrics(results)
    assert m["triage_accuracy"] == 1.0
    assert m["action_selection_accuracy"] == 1.0
    assert m["retrieval_recall_at_3"] == 1.0
    assert "retrieval_precision_at_1" in m
    assert "retrieval_precision_at_3" not in m
    assert "mean_confidence" in m and "pct_below_confidence_floor" in m
    assert "durable_fix_valid_path_rate" in m


def test_write_result_envelope(tmp_path):
    p = _write_result(tmp_path, 2, results=[], metrics={"triage_accuracy": 1.0},
                      model_config={"triage": "claude-haiku-4-5-20251001"})
    payload = json.loads(p.read_text(encoding="utf-8"))
    assert payload["tier"] == 2
    assert "code_git_sha" in payload and "git_sha" not in payload
    assert payload["model_config"]["triage"].startswith("claude")
    assert p.read_text(encoding="utf-8").endswith("\n")


def test_missing_runbooks_detects_gap():
    class _FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, _sql, _params):
            pass

        def fetchall(self):
            # Only 2 of the 4 expected runbooks are present.
            return [("runbook:memory_leak.md",), ("runbook:db_deadlock.md",)]

    class _FakeConn:
        def cursor(self):
            return _FakeCursor()

    missing = _missing_runbooks(_FakeConn())
    assert sorted(missing) == ["runbook:error_spike.md", "runbook:traffic_surge.md"]


def test_representative_cases_one_per_fault_kind():
    from evals.fixtures import load_cases

    reps = _representative_cases(load_cases())
    assert len(reps) == 4
    kinds = {c.incident.fault_kind.value for c in reps}
    assert kinds == {"memory_leak", "db_deadlock", "error_spike", "traffic_surge"}


def test_tier2_cli_runtime_error_is_report_only(monkeypatch, tmp_path, capsys):
    """A runtime error inside the tier-2 run body must never propagate — it's
    caught, reported, and the function returns normally (report-only tiers)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    class _FakeConn:
        def close(self):
            pass

    import evals.run_evals as run_evals_module

    monkeypatch.setattr("retrieval.db.get_conn", lambda: _FakeConn())
    monkeypatch.setattr(run_evals_module, "_missing_runbooks", lambda conn: [])

    def _boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(run_evals_module, "run_tier2", _boom)

    args = argparse.Namespace(out_dir=str(tmp_path), tier3_all=False)
    _run_tier2_cli(args)  # must not raise

    out = capsys.readouterr().out
    assert "[tier2] ERROR: boom" in out

"""Evaluation-tab data derivations (spec section 12): result loading, scorecard, trend."""

import json

import pytest

from dashboard.evals_data import (
    EvalRun,
    headline_scorecard,
    latest_per_tier,
    load_eval_runs,
    metric_series,
    tiering_deltas,
    tiering_table,
)


def _write(directory, name, payload):
    (directory / name).write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def results_dir(tmp_path):
    # tier-1 envelopes use `git_sha`; tiers 2-4 and the sweep use `code_git_sha`.
    _write(tmp_path, "tier1-20260716T061248Z.json",
           {"tier": 1, "git_sha": "aaa1111", "metrics": {"unsafe_blocked_rate": 0.9}})
    _write(tmp_path, "tier1-20260716T122221Z.json",
           {"tier": 1, "git_sha": "bbb2222", "metrics": {"unsafe_blocked_rate": 1.0}})
    _write(tmp_path, "tier2-20260724T152418Z.json",
           {"tier": 2, "code_git_sha": "ccc3333",
            "metrics": {"triage_accuracy": 1.0, "action_selection_accuracy": 1.0,
                        "retrieval_recall_at_3": 1.0, "geval_mean": 0.4}})
    _write(tmp_path, "tier3-20260725T130820Z.json",
           {"tier": 3, "code_git_sha": "ddd4444", "metrics": {"remediation_success_rate": 1.0}})
    _write(tmp_path, "model-tiering-20260725T151246Z.json",
           {"code_git_sha": "eee5555",
            "configs": [
                {"label": "all-sonnet", "triage_model": "claude-sonnet-4-6",
                 "propose_model": "claude-sonnet-4-6",
                 "metrics": {"action_selection_accuracy": 1.0, "total_cost_usd": 0.4648,
                             "mean_latency_ms": 10762.3}},
                {"label": "triage-haiku", "triage_model": "claude-haiku-4-5-20251001",
                 "propose_model": "claude-sonnet-4-6",
                 "metrics": {"action_selection_accuracy": 1.0, "total_cost_usd": 0.3193,
                             "mean_latency_ms": 9000.0}},
            ],
            "comparisons": [{"baseline": "all-sonnet", "candidate": "triage-haiku",
                             "cost_delta_pct": -31.3}]})
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")
    return tmp_path


def test_load_eval_runs_parses_tier_and_timestamp_oldest_first(results_dir):
    runs = load_eval_runs(results_dir)
    assert [(r.tier, r.run_at) for r in runs] == [
        ("1", "20260716T061248Z"),
        ("1", "20260716T122221Z"),
        ("2", "20260724T152418Z"),
        ("3", "20260725T130820Z"),
        ("model-tiering", "20260725T151246Z"),
    ]


def test_load_eval_runs_accepts_both_git_sha_spellings(results_dir):
    by_tier = {r.tier: r for r in load_eval_runs(results_dir)}
    assert by_tier["1"].code_git_sha == "bbb2222"    # from `git_sha`
    assert by_tier["2"].code_git_sha == "ccc3333"    # from `code_git_sha`


def test_load_eval_runs_ignores_non_result_files(results_dir):
    assert all(r.path.suffix == ".json" for r in load_eval_runs(results_dir))
    assert len(load_eval_runs(results_dir)) == 5


def test_load_eval_runs_on_empty_directory_returns_empty(tmp_path):
    assert load_eval_runs(tmp_path) == []


def test_latest_per_tier_keeps_the_newest_run(results_dir):
    latest = latest_per_tier(load_eval_runs(results_dir))
    assert latest["1"].run_at == "20260716T122221Z"
    assert latest["1"].metrics["unsafe_blocked_rate"] == 1.0


def test_headline_scorecard_pulls_each_metric_from_its_owning_tier(results_dir):
    cards = headline_scorecard(latest_per_tier(load_eval_runs(results_dir)))
    values = {c["metric"]: c["value"] for c in cards}
    assert values["unsafe_blocked_rate"] == 1.0        # tier 1
    assert values["triage_accuracy"] == 1.0            # tier 2
    assert values["remediation_success_rate"] == 1.0   # tier 3
    assert values["geval_mean"] == 0.4                 # tier 2, fuzzy rubric


def test_headline_scorecard_reports_none_for_a_tier_never_run(results_dir):
    latest = latest_per_tier(load_eval_runs(results_dir))
    del latest["3"]
    cards = {c["metric"]: c["value"] for c in headline_scorecard(latest)}
    assert cards["remediation_success_rate"] is None


def test_metric_series_returns_one_point_per_run(results_dir):
    runs = load_eval_runs(results_dir)
    series = metric_series(runs, "1", "unsafe_blocked_rate")
    assert series == [
        {"run_at": "20260716T061248Z", "value": 0.9},
        {"run_at": "20260716T122221Z", "value": 1.0},
    ]


def test_metric_series_is_single_point_when_a_tier_ran_once(results_dir):
    assert len(metric_series(load_eval_runs(results_dir), "2", "triage_accuracy")) == 1


def test_metric_series_skips_null_metric_values(tmp_path):
    # geval_mean is null until the DeepEval judge has been run.
    _write(tmp_path, "tier2-20260724T152418Z.json",
           {"tier": 2, "code_git_sha": "x", "metrics": {"geval_mean": None}})
    assert metric_series(load_eval_runs(tmp_path), "2", "geval_mean") == []


def test_tiering_table_flattens_each_config(results_dir):
    run = latest_per_tier(load_eval_runs(results_dir))["model-tiering"]
    table = tiering_table(run)
    assert [r["config"] for r in table] == ["all-sonnet", "triage-haiku"]
    assert table[1]["total_cost_usd"] == pytest.approx(0.3193)
    assert table[1]["triage_model"] == "claude-haiku-4-5-20251001"


def test_tiering_deltas_exposes_the_cost_comparison(results_dir):
    run = latest_per_tier(load_eval_runs(results_dir))["model-tiering"]
    assert tiering_deltas(run) == [
        {"baseline": "all-sonnet", "candidate": "triage-haiku", "cost_delta_pct": -31.3}
    ]


def test_tiering_helpers_tolerate_a_run_without_configs():
    run = EvalRun(tier="model-tiering", run_at="20260725T151246Z", code_git_sha="x",
                  metrics={}, path=None, raw={})
    assert tiering_table(run) == []
    assert tiering_deltas(run) == []

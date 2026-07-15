import json
import subprocess
import sys
from pathlib import Path

from evals.run_evals import load_cases, run_tier1
from evals.metrics import unsafe_blocked_rate, verdict_accuracy

CASES = Path("evals/policy_cases.yaml")


def test_cases_load_and_have_required_fields():
    cases = load_cases(CASES)
    assert len(cases) >= 16
    for c in cases:
        assert c["category"] in ("safe", "unsafe")
        assert c["expected_decision"] in ("allow", "needs_approval", "block")


def test_tier1_run_scores_perfectly_against_current_engine():
    results = run_tier1(load_cases(CASES))
    assert unsafe_blocked_rate(results) == 1.0
    assert verdict_accuracy(results) == 1.0


def test_unsafe_blocked_rate_catches_an_allowed_unsafe_case():
    results = [
        {"id": "x", "category": "unsafe", "expected": "block", "actual": "allow"},
        {"id": "y", "category": "unsafe", "expected": "block", "actual": "block"},
    ]
    assert unsafe_blocked_rate(results) == 0.5


def test_runner_cli_writes_results_json_and_exits_zero(tmp_path):
    proc = subprocess.run(
        [sys.executable, "evals/run_evals.py", "--tier", "1", "--out-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    written = list(tmp_path.glob("tier1-*.json"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text())
    assert payload["metrics"]["unsafe_blocked_rate"] == 1.0
    assert payload["metrics"]["verdict_accuracy"] == 1.0

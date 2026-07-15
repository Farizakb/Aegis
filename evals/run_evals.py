"""Tier-1 policy evals: seeded safe/unsafe proposals -> exact verdicts (ADR-0007).

Pure Python — no LLM, no Docker. Run: python evals/run_evals.py --tier 1
Exit code 0 iff unsafe_blocked_rate == 1.0 and verdict_accuracy == 1.0.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.actions import ProposedAction  # noqa: E402
from agent.state import PolicyDecision, SandboxResult, TriageResult  # noqa: E402
from policy.engine import PolicyEngine  # noqa: E402
from stream.schema import FaultKind  # noqa: E402

BASE_T = 1_000_000.0


def load_cases(path: Path) -> list[dict]:
    cases = yaml.safe_load(path.read_text(encoding="utf-8"))
    for c in cases:
        if "patch_lines" in c["proposal"]:
            c["proposal"]["patch"] = "x\n" * c["proposal"].pop("patch_lines")
    return cases


def _sandbox(spec: str | None) -> SandboxResult | None:
    if spec is None:
        return None
    passed = spec == "pass"
    return SandboxResult(
        passed=passed, exit_code=0 if passed else 1, stdout="", stderr="",
        duration_ms=1.0, before_metrics={"rss_mb": 80.0},
        after_metrics={"rss_mb": 0.0} if passed else {"rss_mb": 82.0},
    )


def _triage(confidence: float | None) -> TriageResult | None:
    if confidence is None:
        return None
    return TriageResult(fault_kind=FaultKind.memory_leak, root_cause="seeded",
                        confidence=confidence, reasoning="eval fixture")


def _seed_history(engine: PolicyEngine, history: list[dict], clock: dict) -> None:
    for h in history:
        clock["t"] = BASE_T - float(h["age_s"])
        extra = {k: h[k] for k in ("workers", "flag_name") if k in h}
        engine.record_apply(
            ProposedAction(action=h["action"], reason="seeded history",
                           target=h.get("target", "mock_app"), **extra),
            PolicyDecision(h["decision"]),
        )
    clock["t"] = BASE_T


def run_tier1(cases: list[dict]) -> list[dict]:
    results = []
    for case in cases:
        clock = {"t": BASE_T}
        engine = PolicyEngine(clock=lambda c=clock: c["t"])
        _seed_history(engine, case.get("history", []), clock)
        verdict = engine.evaluate(
            proposal=case["proposal"],
            sandbox=_sandbox(case.get("sandbox")),
            triage=_triage(case.get("triage_confidence")),
        )
        results.append({
            "id": case["id"],
            "category": case["category"],
            "expected": case["expected_decision"],
            "actual": verdict.decision.value,
            "violated_rules": verdict.violated_rules,
        })
    return results


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


def main() -> int:
    from evals.metrics import unsafe_blocked_rate, verdict_accuracy

    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", choices=["1"], required=True)
    parser.add_argument("--out-dir", default="evals/results")
    args = parser.parse_args()

    cases = load_cases(Path(__file__).parent / "policy_cases.yaml")
    results = run_tier1(cases)
    metrics = {
        "unsafe_blocked_rate": unsafe_blocked_rate(results),
        "verdict_accuracy": verdict_accuracy(results),
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = {"tier": 1, "run_at": stamp, "git_sha": _git_sha(),
               "n_cases": len(results), "metrics": metrics, "results": results}
    (out / f"tier1-{stamp}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    for r in results:
        flag = "OK " if r["actual"] == r["expected"] else "MISS"
        print(f"[{flag}] {r['id']}: expected {r['expected']}, got {r['actual']}")
    print(f"\nunsafe_blocked_rate: {metrics['unsafe_blocked_rate']:.0%}")
    print(f"verdict_accuracy:    {metrics['verdict_accuracy']:.0%}")

    return 0 if metrics["unsafe_blocked_rate"] == 1.0 and metrics["verdict_accuracy"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Tier-1/2 evals runner (ADR-0007).

Tier 1: seeded safe/unsafe proposals -> exact verdicts. Pure Python — no LLM, no Docker.
Tier 2: triage + action-selection + retrieval, replayed through the graph nodes.
Run: python evals/run_evals.py --tier 1|2
Exit code 0 iff tier-1's unsafe_blocked_rate == 1.0 and verdict_accuracy == 1.0.
Tier 2 never affects the exit code.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
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


def _write_result(out_dir: Path, tier: int, results: list[dict], metrics: dict,
                   model_config: dict) -> Path:
    """Shared results envelope for all tiers."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "tier": tier,
        "run_at": stamp,
        "code_git_sha": _git_sha(),
        "n_cases": len(results),
        "model_config": model_config,
        "metrics": metrics,
        "results": results,
    }
    path = out / f"tier{tier}-{stamp}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


async def run_tier2(cases: list, *, triage_llm, propose_llm, search_tool) -> list[dict]:
    """Replay each case through triage->retrieve->propose, then score with a
    DETERMINISTIC retrieval query (locked decision Q6) so P@k/recall@k are
    reproducible and independent of noisy triage output."""
    from evals.drivers import run_propose
    from evals.geval import score_root_cause

    results = []
    for case in cases:
        result = await run_propose(case, triage_llm=triage_llm, propose_llm=propose_llm,
                                    search_tool=search_tool)

        det_query = f"{case.incident.fault_kind.value}: {case.truth['retrieval_root_cause']}"
        det_chunks = await search_tool.ainvoke({"query": det_query, "top_k": 3})
        seen = []
        for c in det_chunks:
            if c["source"] not in seen:
                seen.append(c["source"])
        result["retrieved_sources"] = seen
        result["geval_score"] = score_root_cause(result["root_cause_actual"],
                                                   case.truth["root_cause_reference"])
        results.append(result)
    return results


def _tier2_metrics(results: list[dict]) -> dict:
    from evals.metrics import (
        action_selection_accuracy,
        mean_confidence,
        pct_below_confidence_floor,
        retrieval_precision_at_k,
        retrieval_recall_at_k,
        triage_accuracy,
    )

    geval_scores = [r["geval_score"] for r in results if r.get("geval_score") is not None]

    return {
        "triage_accuracy": triage_accuracy(results),
        "action_selection_accuracy": action_selection_accuracy(results),
        "retrieval_precision_at_3": retrieval_precision_at_k(results, 3),
        "retrieval_recall_at_3": retrieval_recall_at_k(results, 3),
        "mean_confidence": mean_confidence(results),
        "pct_below_confidence_floor": pct_below_confidence_floor(results),
        "geval_mean": (sum(geval_scores) / len(geval_scores)) if geval_scores else None,
    }


def _run_tier2_cli(out_dir: str) -> None:
    """Graceful skip if no API key or Postgres unreachable; never touches exit code."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("SKIPPED (ANTHROPIC_API_KEY not set)")
        return

    from evals.drivers import DirectSearchAdapter
    from evals.fixtures import load_cases as load_tier2_cases
    from retrieval.db import get_conn

    try:
        conn = get_conn()
    except Exception as exc:
        print(f"SKIPPED (Postgres unreachable: {exc})")
        return

    try:
        from agent.llm import get_propose_llm, get_triage_llm

        triage_llm = get_triage_llm()
        propose_llm = get_propose_llm()
        search_tool = DirectSearchAdapter(conn)

        cases = load_tier2_cases()
        results = asyncio.run(run_tier2(cases, triage_llm=triage_llm, propose_llm=propose_llm,
                                         search_tool=search_tool))
        metrics = _tier2_metrics(results)
        model_config = {"triage": triage_llm.model, "propose": propose_llm.model}
        path = _write_result(Path(out_dir), 2, results, metrics, model_config)

        for r in results:
            flag = "OK " if r["fault_kind_actual"] == r["fault_kind_expected"] else "MISS"
            print(f"[{flag}] {r['id']}: expected {r['fault_kind_expected']}, got {r['fault_kind_actual']}")
        print(f"\ntriage_accuracy:           {metrics['triage_accuracy']:.0%}")
        print(f"action_selection_accuracy: {metrics['action_selection_accuracy']:.0%}")
        print(f"retrieval_precision_at_3:  {metrics['retrieval_precision_at_3']:.0%}")
        print(f"retrieval_recall_at_3:     {metrics['retrieval_recall_at_3']:.0%}")
        print(f"wrote {path}")
    finally:
        conn.close()


def main() -> int:
    from evals.metrics import unsafe_blocked_rate, verdict_accuracy

    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", choices=["1", "2"], required=True)
    parser.add_argument("--out-dir", default="evals/results")
    args = parser.parse_args()

    if args.tier == "2":
        _run_tier2_cli(args.out_dir)
        return 0

    cases = load_cases(Path(__file__).parent / "policy_cases.yaml")
    results = run_tier1(cases)
    metrics = {
        "unsafe_blocked_rate": unsafe_blocked_rate(results),
        "verdict_accuracy": verdict_accuracy(results),
    }

    _write_result(Path(args.out_dir), 1, results, metrics, model_config={})

    for r in results:
        flag = "OK " if r["actual"] == r["expected"] else "MISS"
        print(f"[{flag}] {r['id']}: expected {r['expected']}, got {r['actual']}")
    print(f"\nunsafe_blocked_rate: {metrics['unsafe_blocked_rate']:.0%}")
    print(f"verdict_accuracy:    {metrics['verdict_accuracy']:.0%}")

    return 0 if metrics["unsafe_blocked_rate"] == 1.0 and metrics["verdict_accuracy"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

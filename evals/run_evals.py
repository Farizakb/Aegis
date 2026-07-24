"""Tier-1/2/3/4 evals runner (ADR-0007).

Tier 1: seeded safe/unsafe proposals -> exact verdicts. Pure Python — no LLM, no Docker.
Tier 2: triage + action-selection + retrieval, replayed through the graph nodes.
Tier 3: proposed mitigation -> empirical fault replay in the Docker sandbox.
Tier 4: 3-4 incidents through the WHOLE compiled graph with an AutoApprover -> final outcome.
Run: python evals/run_evals.py --tier 1|2|3|4
Exit code 0 iff tier-1's unsafe_blocked_rate == 1.0 and verdict_accuracy == 1.0.
Tiers 2, 3, and 4 never affect the exit code.
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
from hitl.auto import AutoApprover  # noqa: E402
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
        # Run the (sync, DeepEval-internal-event-loop) judge in a worker thread so it
        # gets its own fresh event loop instead of nesting inside this async run.
        result["geval_score"] = await asyncio.to_thread(
            score_root_cause, result["root_cause_actual"], case.truth["root_cause_reference"]
        )
        results.append(result)
    return results


def _tier2_metrics(results: list[dict]) -> dict:
    from evals.metrics import (
        action_selection_accuracy,
        durable_fix_valid_path_rate,
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
        "retrieval_recall_at_3": retrieval_recall_at_k(results, 3),
        "retrieval_precision_at_1": retrieval_precision_at_k(results, 1),
        "mean_confidence": mean_confidence(results),
        "pct_below_confidence_floor": pct_below_confidence_floor(results),
        "geval_mean": (sum(geval_scores) / len(geval_scores)) if geval_scores else None,
        "durable_fix_valid_path_rate": durable_fix_valid_path_rate(results),
    }


EXPECTED_RUNBOOK_SOURCES = [
    "runbook:memory_leak.md",
    "runbook:db_deadlock.md",
    "runbook:error_spike.md",
    "runbook:traffic_surge.md",
]


def _missing_runbooks(conn) -> list[str]:
    """Reproducibility guard: which of the 4 expected fault-kind runbooks are
    absent from the ingested `chunks` corpus. Non-fatal — callers warn and
    continue; a missing runbook just means that fixture's recall scores 0."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT source FROM chunks WHERE source = ANY(%s)",
            (EXPECTED_RUNBOOK_SOURCES,),
        )
        found = {row[0] for row in cur.fetchall()}
    return [s for s in EXPECTED_RUNBOOK_SOURCES if s not in found]


def _representative_cases(cases: list) -> list:
    """First case per distinct fault_kind, preserving fixture order — the
    default tier-3 subset (one live sandbox replay per fault kind instead of
    all fixtures) so a routine run stays minutes, not tens of minutes."""
    seen = set()
    out = []
    for c in cases:
        fk = c.incident.fault_kind
        if fk not in seen:
            seen.add(fk)
            out.append(c)
    return out


def _dependency_status() -> dict:
    """Cheap, exception-swallowing probes for the optional eval dependencies,
    used to decide tier skips in `--tier all` (and each tier's own CLI)."""
    status = {
        "anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "postgres": False,
        "docker": False,
    }

    try:
        from retrieval.db import get_conn

        get_conn().close()
        status["postgres"] = True
    except Exception:
        pass

    try:
        import docker

        docker.from_env().ping()
        status["docker"] = True
    except Exception:
        pass

    return status


def _run_tier2_cli(args) -> None:
    """Graceful skip if required dependencies are unavailable; never touches exit code.
    Any OTHER runtime error during the run is caught and reported, never raised —
    tiers 2/3 are report-only and must never abort the process."""
    status = _dependency_status()
    missing = [dep for dep in ("anthropic_key", "postgres") if not status[dep]]
    if missing:
        print(f"[tier2] SKIPPED (missing: {', '.join(missing)})")
        return

    from evals.drivers import DirectSearchAdapter
    from evals.fixtures import load_cases as load_tier2_cases
    from retrieval.db import get_conn

    conn = None

    try:
        conn = get_conn()
        missing_rb = _missing_runbooks(conn)
        if missing_rb:
            print(f"[tier2] WARNING: runbooks not ingested: {missing_rb} — run "
                  "`python -m retrieval.ingest`; affected fixtures will score retrieval recall 0")

        from agent.llm import get_propose_llm, get_triage_llm

        triage_llm = get_triage_llm()
        propose_llm = get_propose_llm()
        search_tool = DirectSearchAdapter(conn)

        cases = load_tier2_cases()
        results = asyncio.run(run_tier2(cases, triage_llm=triage_llm, propose_llm=propose_llm,
                                         search_tool=search_tool))
        metrics = _tier2_metrics(results)
        model_config = {"triage": triage_llm.model, "propose": propose_llm.model}
        path = _write_result(Path(args.out_dir), 2, results, metrics, model_config)

        for r in results:
            flag = "OK " if r["fault_kind_actual"] == r["fault_kind_expected"] else "MISS"
            print(f"[{flag}] {r['id']}: expected {r['fault_kind_expected']}, got {r['fault_kind_actual']}")
        print(f"\ntriage_accuracy:           {metrics['triage_accuracy']:.0%}")
        print(f"action_selection_accuracy: {metrics['action_selection_accuracy']:.0%}")
        print(f"retrieval_precision_at_1:  {metrics['retrieval_precision_at_1']:.0%}")
        print(f"retrieval_recall_at_3:     {metrics['retrieval_recall_at_3']:.0%}")
        print(f"wrote {path}")
    except Exception as exc:  # report-only: a tier run never gates the process
        print(f"[tier2] ERROR: {exc}")
        return
    finally:
        if conn is not None:
            conn.close()


async def run_tier3(cases: list, *, triage_llm, propose_llm, search_tool, executor) -> list[dict]:
    """Replay each case's proposed mitigation through the Docker sandbox and
    score whether it empirically clears the injected fault (ADR-0002)."""
    from evals.drivers import drive_propose

    results = []
    for case in cases:
        result, plan, _usages = await drive_propose(case, triage_llm=triage_llm, propose_llm=propose_llm,
                                                     search_tool=search_tool)
        sbx = await executor.verify(plan.mitigation, case.incident.fault_kind)
        results.append({
            "id": result["id"],
            "mitigation_actual": result["mitigation_actual"],
            "fault_kind": case.incident.fault_kind.value,
            "sandbox_passed": sbx.passed,
            "before_metrics": sbx.before_metrics,
            "after_metrics": sbx.after_metrics,
        })
    return results


def _tier3_metrics(results: list[dict]) -> dict:
    from evals.metrics import remediation_success_rate

    return {"remediation_success_rate": remediation_success_rate(results)}


def _run_tier3_cli(args) -> None:
    """Graceful skip if required dependencies are unavailable; never touches exit
    code. Any OTHER runtime error during the run is caught and reported, never
    raised — tiers 2/3 are report-only and must never abort the process."""
    status = _dependency_status()
    missing = [dep for dep in ("anthropic_key", "postgres", "docker") if not status[dep]]
    if missing:
        print(f"[tier3] SKIPPED (missing: {', '.join(missing)})")
        return

    from evals.drivers import DirectSearchAdapter
    from evals.fixtures import load_cases as load_tier3_cases
    from retrieval.db import get_conn

    conn = None

    try:
        conn = get_conn()
        missing_rb = _missing_runbooks(conn)
        if missing_rb:
            print(f"[tier3] WARNING: runbooks not ingested: {missing_rb} — run "
                  "`python -m retrieval.ingest`; affected fixtures will score retrieval recall 0")

        import docker

        docker_client = docker.from_env()

        from agent.llm import get_propose_llm, get_triage_llm
        from sandbox.executor import SandboxExecutor

        triage_llm = get_triage_llm()
        propose_llm = get_propose_llm()
        search_tool = DirectSearchAdapter(conn)
        executor = SandboxExecutor(docker_client)

        cases = load_tier3_cases()
        cases = cases if args.tier3_all else _representative_cases(cases)
        print(f"[tier3] running {len(cases)} case(s)"
              + ("" if args.tier3_all else " (representative subset; use --tier3-all for all)"))
        results = asyncio.run(run_tier3(cases, triage_llm=triage_llm, propose_llm=propose_llm,
                                         search_tool=search_tool, executor=executor))
        metrics = _tier3_metrics(results)
        model_config = {"triage": triage_llm.model, "propose": propose_llm.model}
        path = _write_result(Path(args.out_dir), 3, results, metrics, model_config)

        for r in results:
            flag = "OK " if r["sandbox_passed"] else "MISS"
            print(f"[{flag}] {r['id']}: {r['mitigation_actual']} on {r['fault_kind']}")
        print(f"\nremediation_success_rate: {metrics['remediation_success_rate']:.0%}")
        print(f"wrote {path}")
    except Exception as exc:  # report-only: a tier run never gates the process
        print(f"[tier3] ERROR: {exc}")
        return
    finally:
        if conn is not None:
            conn.close()


def _expected_outcome(expected_policy: str) -> str:
    """Map ground-truth expected_policy -> the outcome the e2e run should land on
    under an AutoApprover (needs_approval is always approved, so it applies too)."""
    return {"block": "blocked", "needs_approval": "applied", "allow": "applied"}[expected_policy]


class _NoopApplier:
    def apply(self, action) -> None:
        pass


class _NoopSink:
    def emit(self, report) -> None:
        pass


class _NoopRegistry:
    def file(self, *, incident, triage, fix) -> None:
        pass


async def run_tier4(cases: list, *, triage_llm, propose_llm, search_tool, executor) -> list[dict]:
    """Run each case through the WHOLE compiled graph with a real PolicyEngine
    and an AutoApprover so the apply path runs unattended, then score the
    final outcome against ground truth (ADR-0006's full lifecycle, tier-4 style)."""
    from agent.graph import build_graph
    from agent.state import initial_state

    results = []
    for case in cases:
        graph = build_graph(
            triage_llm, propose_llm, search_tool, executor,
            PolicyEngine(), AutoApprover(), _NoopApplier(), _NoopSink(), _NoopRegistry(),
        )
        result = await graph.ainvoke(initial_state(case.incident))
        report = result["report"]
        results.append({
            "id": case.id,
            "fault_kind": case.incident.fault_kind.value,
            "outcome_actual": report.outcome.value,
            "outcome_expected": _expected_outcome(case.truth["expected_policy"]),
            "policy_decision": report.policy_decision.value if report.policy_decision else None,
            "expected_policy": case.truth["expected_policy"],
        })
    return results


def _tier4_metrics(results: list[dict]) -> dict:
    if not results:
        return {"e2e_outcome_accuracy": 1.0}
    matches = sum(1 for r in results if r["outcome_actual"] == r["outcome_expected"])
    return {"e2e_outcome_accuracy": matches / len(results)}


def _run_tier4_cli(args) -> None:
    """Graceful skip if required dependencies are unavailable; never touches exit
    code. Any OTHER runtime error during the run is caught and reported, never
    raised — tier 4 is report-only and must never abort the process."""
    status = _dependency_status()
    missing = [dep for dep in ("anthropic_key", "postgres", "docker") if not status[dep]]
    if missing:
        print(f"[tier4] SKIPPED (missing: {', '.join(missing)})")
        return

    from evals.drivers import DirectSearchAdapter
    from evals.fixtures import load_cases as load_tier4_cases
    from retrieval.db import get_conn

    conn = None

    try:
        conn = get_conn()
        missing_rb = _missing_runbooks(conn)
        if missing_rb:
            print(f"[tier4] WARNING: runbooks not ingested: {missing_rb} — run "
                  "`python -m retrieval.ingest`; affected fixtures will score retrieval recall 0")

        import docker

        docker_client = docker.from_env()

        from agent.llm import get_propose_llm, get_triage_llm
        from sandbox.executor import SandboxExecutor

        triage_llm = get_triage_llm()
        propose_llm = get_propose_llm()
        search_tool = DirectSearchAdapter(conn)
        executor = SandboxExecutor(docker_client)

        cases = _representative_cases(load_tier4_cases())
        print(f"[tier4] running {len(cases)} case(s) (representative subset)")
        results = asyncio.run(run_tier4(cases, triage_llm=triage_llm, propose_llm=propose_llm,
                                         search_tool=search_tool, executor=executor))
        metrics = _tier4_metrics(results)
        model_config = {"triage": triage_llm.model, "propose": propose_llm.model}
        path = _write_result(Path(args.out_dir), 4, results, metrics, model_config)

        for r in results:
            flag = "OK " if r["outcome_actual"] == r["outcome_expected"] else "MISS"
            print(f"[{flag}] {r['id']}: expected {r['outcome_expected']}, got {r['outcome_actual']}")
        print(f"\ne2e_outcome_accuracy: {metrics['e2e_outcome_accuracy']:.0%}")
        print(f"wrote {path}")
    except Exception as exc:  # report-only: a tier run never gates the process
        print(f"[tier4] ERROR: {exc}")
        return
    finally:
        if conn is not None:
            conn.close()


def _run_tier1_cli(args) -> int:
    """Run tier 1 (pure Python, always runnable) and return its gate exit code:
    0 iff unsafe_blocked_rate == 1.0 and verdict_accuracy == 1.0."""
    from evals.metrics import unsafe_blocked_rate, verdict_accuracy

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


def _dispatch(args) -> int:
    """Route a parsed args namespace to the right tier(s). Tier 1 is the sole
    exit-code gate; tiers 2-4 are report-only and never affect the return value."""
    if args.tier == "2":
        _run_tier2_cli(args)
        return 0

    if args.tier == "3":
        _run_tier3_cli(args)
        return 0

    if args.tier == "4":
        _run_tier4_cli(args)
        return 0

    if args.tier == "all":
        status = _dependency_status()
        print(f"[deps] anthropic_key={status['anthropic_key']} "
              f"postgres={status['postgres']} docker={status['docker']}")

        exit_code = _run_tier1_cli(args)
        _run_tier2_cli(args)
        _run_tier3_cli(args)
        _run_tier4_cli(args)
        return exit_code

    return _run_tier1_cli(args)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", choices=["1", "2", "3", "4", "all"], required=True)
    parser.add_argument("--out-dir", default="evals/results")
    parser.add_argument("--tier3-all", action="store_true", default=False,
                        help="Run all fixtures in tier 3 instead of the default "
                             "one-per-fault-kind representative subset.")
    args = parser.parse_args()

    return _dispatch(args)


if __name__ == "__main__":
    raise SystemExit(main())

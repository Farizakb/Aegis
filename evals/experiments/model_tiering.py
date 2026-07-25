"""Model-tiering experiment: compare per-node model choices on accuracy and
cost (ADR-0010). Sweeps the 2x2 grid over {triage, propose} x {Sonnet, Haiku}
(see `FEATURED_CONFIGS`), with `all-sonnet` as the most-capable baseline. Each
cheaper config is compared to that baseline in `comparisons`; the singular
`comparison` keeps the headline pair (baseline vs the spec-default triage-haiku).

Every config keeps a trimmed per-case `results` list so the artifact is
self-verifying — a reviewer can see exactly WHICH incident's action selection
differs between configs, rather than trusting a bare aggregate delta.

Note: `mean_latency_ms` is environment-dependent (network conditions, API
load) and NOT reproducible across runs. `total_cost_usd` is a deterministic
function of token counts and the static pricing table (observability/pricing.py),
so it IS reproducible and is the metric the headline claim rests on. Accuracy
deltas are single-run and can sit within sampling noise (n=18) — read the
per-case `results` before treating a small delta as a real regression.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from agent.llm import get_llm
from evals.drivers import drive_propose
from evals.metrics import action_selection_accuracy, triage_accuracy
from evals.run_evals import _dependency_status, _git_sha
from observability.langsmith import experiment_env
from observability.pricing import total_cost_usd

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"

SONNET = "claude-sonnet-4-6"
HAIKU = "claude-haiku-4-5-20251001"

# The 2x2 grid. Order matters: configs[0] (all-sonnet) is the most-capable
# baseline every other config is compared against; configs[1] (triage-haiku,
# the spec default) is the singular `comparison`'s headline candidate.
FEATURED_CONFIGS = [
    {"label": "all-sonnet", "triage_model": SONNET, "propose_model": SONNET},
    {"label": "triage-haiku", "triage_model": HAIKU, "propose_model": SONNET},
    {"label": "propose-haiku", "triage_model": SONNET, "propose_model": HAIKU},
    {"label": "all-haiku", "triage_model": HAIKU, "propose_model": HAIKU},
]


def _case_record(r: dict) -> dict:
    """Trimmed per-case row for the artifact: enough to see WHICH incident's
    action selection differs between configs, without dumping full reasoning."""
    return {
        "id": r["id"],
        "fault_kind_actual": r["fault_kind_actual"],
        "confidence": r["confidence"],
        "mitigation_actual": r["mitigation_actual"],
        "acceptable_mitigations": r["acceptable_mitigations"],
        "action_correct": r["mitigation_actual"] in r["acceptable_mitigations"],
    }


def _compare(base: dict, cand: dict) -> dict:
    """Delta of a candidate config against the baseline. cost_delta is the
    reproducible headline; accuracy deltas are single-run (may be noise)."""
    bm, cm = base["metrics"], cand["metrics"]
    base_cost, cand_cost = bm["total_cost_usd"], cm["total_cost_usd"]
    return {
        "baseline": base["label"],
        "candidate": cand["label"],
        "cost_delta_usd": cand_cost - base_cost,
        "cost_delta_pct": ((cand_cost - base_cost) / base_cost * 100) if base_cost else None,
        "triage_accuracy_delta": cm["triage_accuracy"] - bm["triage_accuracy"],
        "action_selection_accuracy_delta": (
            cm["action_selection_accuracy"] - bm["action_selection_accuracy"]
        ),
    }


async def run_experiment(cases, configs: list[dict], *, search_tool) -> dict:
    """Run every case through drive_propose under each config, scoring
    accuracy and cost per config. Pure driver logic — no filesystem writes."""
    config_results = []
    for cfg in configs:
        triage_llm = get_llm(cfg["triage_model"])
        propose_llm = get_llm(cfg["propose_model"])

        results = []
        all_usages = []
        for case in cases:
            result, _plan, usages = await drive_propose(
                case, triage_llm=triage_llm, propose_llm=propose_llm, search_tool=search_tool
            )
            results.append(result)
            all_usages.extend(usages)

        latencies = [u.latency_ms for u in all_usages]
        metrics = {
            "triage_accuracy": triage_accuracy(results),
            "action_selection_accuracy": action_selection_accuracy(results),
            "total_cost_usd": total_cost_usd(all_usages),
            "mean_latency_ms": (sum(latencies) / len(latencies)) if latencies else 0.0,
        }
        config_results.append({
            "label": cfg["label"],
            "triage_model": cfg["triage_model"],
            "propose_model": cfg["propose_model"],
            "metrics": metrics,
            "results": [_case_record(r) for r in results],
        })

    # Singular `comparison` = baseline vs the first candidate (headline pair);
    # `comparisons` = every non-baseline config vs the baseline (the full sweep).
    comparison = _compare(config_results[0], config_results[1]) if len(config_results) >= 2 else None
    comparisons = [_compare(config_results[0], c) for c in config_results[1:]]

    return {
        "run_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "code_git_sha": _git_sha(),
        "configs": config_results,
        "comparison": comparison,
        "comparisons": comparisons,
    }


def write_experiment(payload: dict, out_dir: Path = RESULTS_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"model-tiering-{payload['run_at']}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> None:
    """Graceful skip (print + return, no write) if the API key is unset or
    Postgres is unreachable — mirrors evals/run_evals.py's tier-2 CLI pattern."""
    status = _dependency_status()
    missing = [dep for dep in ("anthropic_key", "postgres") if not status[dep]]
    if missing:
        print(f"[model-tiering] SKIPPED (missing: {', '.join(missing)})")
        return

    from evals.drivers import DirectSearchAdapter
    from evals.fixtures import load_cases
    from retrieval.db import get_conn

    conn = None
    try:
        conn = get_conn()
        search_tool = DirectSearchAdapter(conn)
        cases = load_cases()

        os.environ.update(experiment_env("model-tiering", {"triage": FEATURED_CONFIGS[0]["triage_model"]}))

        payload = asyncio.run(run_experiment(cases, FEATURED_CONFIGS, search_tool=search_tool))
        path = write_experiment(payload)

        header = f"{'label':<16}{'triage_acc':<12}{'action_acc':<12}{'cost_usd':<12}{'mean_lat_ms':<12}"
        print(header)
        for cfg in payload["configs"]:
            m = cfg["metrics"]
            print(f"{cfg['label']:<16}{m['triage_accuracy']:<12.0%}"
                  f"{m['action_selection_accuracy']:<12.0%}"
                  f"${m['total_cost_usd']:<11.4f}{m['mean_latency_ms']:<12.1f}")

        for cmp in payload["comparisons"]:
            pct = f"{cmp['cost_delta_pct']:+.1f}%" if cmp["cost_delta_pct"] is not None else "n/a"
            print(f"{cmp['candidate']:<14} vs {cmp['baseline']}: "
                  f"cost {cmp['cost_delta_usd']:+.4f} ({pct}), "
                  f"triage_acc {cmp['triage_accuracy_delta']:+.0%}, "
                  f"action_acc {cmp['action_selection_accuracy_delta']:+.0%}")
        print(f"wrote {path}")
    except Exception as exc:  # report-only: never raise out of an experiment run
        print(f"[model-tiering] ERROR: {exc}")
        return
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()

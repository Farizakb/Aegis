"""Model-tiering experiment: compare per-node model choices on accuracy and
cost (ADR-0010). Featured comparison (see `main`): triage Haiku vs Sonnet,
propose held at Sonnet — proves a cheaper triage model doesn't cost triage
accuracy while cutting $ per incident.

Note: `mean_latency_ms` is environment-dependent (network conditions, API
load) and NOT reproducible across runs. `total_cost_usd` is a deterministic
function of token counts and the static pricing table (observability/pricing.py),
so it IS reproducible and is the metric the headline claim rests on.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from agent.llm import get_llm
from evals.drivers import drive_propose
from evals.metrics import action_selection_accuracy, triage_accuracy
from evals.run_evals import _dependency_status, _git_sha
from observability.pricing import total_cost_usd

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"

FEATURED_CONFIGS = [
    {"label": "triage-sonnet", "triage_model": "claude-sonnet-4-6",
     "propose_model": "claude-sonnet-4-6"},
    {"label": "triage-haiku", "triage_model": "claude-haiku-4-5-20251001",
     "propose_model": "claude-sonnet-4-6"},
]


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
        })

    comparison = None
    if len(config_results) >= 2:
        base_metrics = config_results[0]["metrics"]
        cand_metrics = config_results[1]["metrics"]
        base_cost = base_metrics["total_cost_usd"]
        cand_cost = cand_metrics["total_cost_usd"]
        comparison = {
            "baseline": config_results[0]["label"],
            "candidate": config_results[1]["label"],
            "cost_delta_usd": cand_cost - base_cost,
            "cost_delta_pct": ((cand_cost - base_cost) / base_cost * 100) if base_cost else None,
            "triage_accuracy_delta": cand_metrics["triage_accuracy"] - base_metrics["triage_accuracy"],
            "action_selection_accuracy_delta": (
                cand_metrics["action_selection_accuracy"] - base_metrics["action_selection_accuracy"]
            ),
        }

    return {
        "run_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "code_git_sha": _git_sha(),
        "configs": config_results,
        "comparison": comparison,
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

        payload = asyncio.run(run_experiment(cases, FEATURED_CONFIGS, search_tool=search_tool))
        path = write_experiment(payload)

        header = f"{'label':<16}{'triage_acc':<12}{'action_acc':<12}{'cost_usd':<12}{'mean_lat_ms':<12}"
        print(header)
        for cfg in payload["configs"]:
            m = cfg["metrics"]
            print(f"{cfg['label']:<16}{m['triage_accuracy']:<12.0%}"
                  f"{m['action_selection_accuracy']:<12.0%}"
                  f"${m['total_cost_usd']:<11.4f}{m['mean_latency_ms']:<12.1f}")

        cmp = payload["comparison"]
        if cmp:
            pct = f"{cmp['cost_delta_pct']:+.1f}%" if cmp["cost_delta_pct"] is not None else "n/a"
            print(f"comparison: {cmp['candidate']} vs {cmp['baseline']} -> "
                  f"cost_delta_usd={cmp['cost_delta_usd']:+.4f} ({pct}), "
                  f"triage_accuracy_delta={cmp['triage_accuracy_delta']:+.0%}, "
                  f"action_selection_accuracy_delta={cmp['action_selection_accuracy_delta']:+.0%}")
        print(f"wrote {path}")
    except Exception as exc:  # report-only: never raise out of an experiment run
        print(f"[model-tiering] ERROR: {exc}")
        return
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()

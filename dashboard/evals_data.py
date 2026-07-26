"""Evaluation-tab data access: timestamped eval-result JSONs (spec section 12, ADR-0011).

Pure file loading and derivations — this module MUST NOT import streamlit.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parents[1] / "evals" / "results"

# tier1-20260716T122221Z.json / model-tiering-20260725T151246Z.json
_FILENAME_RE = re.compile(r"^(?P<tier>.+)-(?P<run_at>\d{8}T\d{6}Z)\.json$")

# (display label, owning tier, metric key). The five headline metrics of spec section 15;
# `mean_confidence`, `pct_below_confidence_floor` and `durable_fix_valid_path_rate` are
# diagnostics, deliberately NOT headlines.
HEADLINE_METRICS: list[tuple[str, str, str]] = [
    ("Unsafe actions blocked", "1", "unsafe_blocked_rate"),
    ("Triage accuracy", "2", "triage_accuracy"),
    ("Action-selection accuracy", "2", "action_selection_accuracy"),
    ("Retrieval recall@3", "2", "retrieval_recall_at_3"),
    ("Remediation success", "3", "remediation_success_rate"),
]

# Scored 0-1 by a G-Eval rubric, not a percentage — rendered differently on purpose.
FUZZY_METRIC: tuple[str, str, str] = ("Root-cause reasoning (G-Eval)", "2", "geval_mean")


@dataclass(frozen=True)
class EvalRun:
    tier: str
    run_at: str
    code_git_sha: str | None
    metrics: dict
    path: Path | None
    raw: dict


def _normalize_tier(prefix: str) -> str:
    """"tier2" -> "2"; "model-tiering" stays as-is."""
    return prefix[len("tier"):] if prefix.startswith("tier") else prefix


def load_eval_runs(results_dir: Path | None = None) -> list[EvalRun]:
    """Every result run, oldest first. `run_at` strings sort lexically = chronologically."""
    directory = results_dir or RESULTS_DIR
    runs: list[EvalRun] = []
    for path in sorted(directory.glob("*.json")):
        match = _FILENAME_RE.match(path.name)
        if match is None:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        runs.append(EvalRun(
            tier=_normalize_tier(match.group("tier")),
            run_at=match.group("run_at"),
            # tier-1 writes `git_sha`; tiers 2-4 and the sweep write `code_git_sha`.
            code_git_sha=payload.get("code_git_sha") or payload.get("git_sha"),
            metrics=payload.get("metrics", {}),
            path=path,
            raw=payload,
        ))
    return sorted(runs, key=lambda run: run.run_at)


def latest_per_tier(runs: list[EvalRun]) -> dict[str, EvalRun]:
    """Newest run per tier. Input is ascending by `run_at`, so the last write wins."""
    latest: dict[str, EvalRun] = {}
    for run in runs:
        latest[run.tier] = run
    return latest


def headline_scorecard(latest: dict[str, EvalRun]) -> list[dict]:
    """The five headline metrics plus the fuzzy rubric, with `None` where a tier is unrun."""
    cards = []
    for label, tier, key in [*HEADLINE_METRICS, FUZZY_METRIC]:
        run = latest.get(tier)
        cards.append({
            "label": label,
            "tier": tier,
            "metric": key,
            "value": run.metrics.get(key) if run else None,
            "run_at": run.run_at if run else None,
            "code_git_sha": run.code_git_sha if run else None,
        })
    return cards


def metric_series(runs: list[EvalRun], tier: str, metric: str) -> list[dict]:
    """Run-over-run points for one metric; may legitimately be length 0 or 1."""
    return [
        {"run_at": run.run_at, "value": run.metrics[metric]}
        for run in runs
        if run.tier == tier and run.metrics.get(metric) is not None
    ]


def tiering_table(run: EvalRun) -> list[dict]:
    """One row per model-tiering config: models, accuracy, cost, latency."""
    rows = []
    for config in run.raw.get("configs", []):
        metrics = config.get("metrics", {})
        rows.append({
            "config": config.get("label"),
            "triage_model": config.get("triage_model"),
            "propose_model": config.get("propose_model"),
            "triage_accuracy": metrics.get("triage_accuracy"),
            "action_selection_accuracy": metrics.get("action_selection_accuracy"),
            "total_cost_usd": metrics.get("total_cost_usd"),
            "mean_latency_ms": metrics.get("mean_latency_ms"),
        })
    return rows


def tiering_deltas(run: EvalRun) -> list[dict]:
    """Every candidate config compared against the baseline."""
    return list(run.raw.get("comparisons", []))

"""Operations-tab data access: incident reports from Postgres (spec section 12, ADR-0011).

Pure data access and derivations — this module MUST NOT import streamlit, so it can
be unit-tested without the [dashboard] extra installed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import psycopg

from agent.state import HitlChoice, IncidentReport, NodeUsage
from observability.pricing import total_cost_usd
from retrieval.db import conn_string

# A promoted durable-fix run persists under "<incident_id>#promo" (Phase 4 upsert-collision
# fix). Its presence is how we know a drafted fix has already been actioned.
PROMOTED_SUFFIX = "#promo"

SELECT_SQL = """
SELECT incident_id, fault_kind, outcome, report, created_at
FROM incident_reports
ORDER BY created_at DESC;
"""


@dataclass(frozen=True)
class IncidentRow:
    incident_id: str
    fault_kind: str
    outcome: str
    created_at: datetime
    report: IncidentReport


def fetch_incident_rows(conn_str: str | None = None) -> list[IncidentRow]:
    """Every persisted incident report, newest first."""
    with psycopg.connect(conn_str or conn_string(), connect_timeout=5) as conn:
        raw = conn.execute(SELECT_SQL).fetchall()
    rows: list[IncidentRow] = []
    for incident_id, fault_kind, outcome, report_json, created_at in raw:
        # psycopg3 decodes JSONB to dict; tolerate a text column too.
        payload = report_json if isinstance(report_json, dict) else json.loads(report_json)
        rows.append(IncidentRow(
            incident_id=incident_id,
            fault_kind=fault_kind,
            outcome=outcome,
            created_at=created_at,
            report=IncidentReport.model_validate(payload),
        ))
    return rows


def outcome_breakdown(rows: list[IncidentRow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.outcome] = counts.get(row.outcome, 0) + 1
    return counts


def open_durable_fixes(rows: list[IncidentRow]) -> list[dict]:
    """Drafted durable fixes still awaiting promotion (spec sections 5 and 12).

    The live DurableFixRegistry is in-memory and process-local to the agent, so the
    queue is derived from Postgres instead: a drafted fix is OPEN unless a promoted
    run for the same base incident exists.
    """
    promoted_bases = {
        row.incident_id.split(PROMOTED_SUFFIX)[0]
        for row in rows if PROMOTED_SUFFIX in row.incident_id
    }
    open_fixes: list[dict] = []
    for row in rows:
        fix = row.report.durable_fix
        if fix is None or PROMOTED_SUFFIX in row.incident_id:
            continue
        if row.incident_id in promoted_bases:
            continue
        open_fixes.append({
            "incident_id": row.incident_id,
            "fault_kind": row.fault_kind,
            "outcome": row.outcome,
            "action": fix.action.value,
            "target_file": fix.target_file,
            "reason": fix.reason,
            "filed_at": row.created_at,
        })
    return open_fixes


def expired_approvals(rows: list[IncidentRow]) -> list[IncidentRow]:
    """Incidents whose approval window elapsed unanswered (spec section 8 TTL)."""
    return [row for row in rows if row.report.hitl_choice is HitlChoice.expired]


def incident_cost_usd(report: IncidentReport) -> float:
    return total_cost_usd(report.node_usage)


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile over a non-empty list. `pct` in [0, 100]."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct / 100.0
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def node_usage_stats(rows: list[IncidentRow]) -> list[dict]:
    """Per-node latency percentiles and cost across all incidents (spec section 12).

    Sourced from real incident telemetry rather than eval fixtures.
    """
    by_node: dict[str, list[NodeUsage]] = {}
    for row in rows:
        for usage in row.report.node_usage:
            by_node.setdefault(usage.node, []).append(usage)
    stats = []
    for node, usages in by_node.items():
        latencies = [u.latency_ms for u in usages]
        stats.append({
            "node": node,
            "calls": len(usages),
            "p50_latency_ms": _percentile(latencies, 50),
            "p95_latency_ms": _percentile(latencies, 95),
            "total_cost_usd": total_cost_usd(usages),
        })
    return sorted(stats, key=lambda s: s["total_cost_usd"], reverse=True)

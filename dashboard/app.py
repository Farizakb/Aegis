"""Aegis dashboard (spec section 12, ADR-0011): Operations from Postgres, Evaluation from
eval-result JSONs.

Host-run, zero-config against the compose stack:

    streamlit run dashboard/app.py
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.operations_data import (
    IncidentRow,
    expired_approvals,
    fetch_incident_rows,
    incident_cost_usd,
    node_usage_stats,
    open_durable_fixes,
    outcome_breakdown,
)

CACHE_TTL_S = 30
OUTCOMES = ("applied", "rejected", "blocked", "escalated")


@st.cache_data(ttl=CACHE_TTL_S, show_spinner=False)
def _incident_rows() -> list[IncidentRow]:
    return fetch_incident_rows()


def _render_operations() -> None:
    try:
        rows = _incident_rows()
    except Exception as exc:  # Postgres down / stack not up: explain, never traceback
        st.error(f"Cannot read incident reports from Postgres: {exc}")
        st.caption("Bring the stack up with `docker compose up -d postgres`, then hit Refresh.")
        return

    if not rows:
        st.info("No incidents yet.")
        st.caption(
            "Trigger a fault and run the agent:\n\n"
            "`curl -X POST http://localhost:8000/faults/memory_leak/trigger`\n\n"
            "`python -m agent.main --count 1`"
        )
        return

    counts = outcome_breakdown(rows)
    columns = st.columns(len(OUTCOMES) + 1)
    columns[0].metric("Incidents", len(rows))
    for column, outcome in zip(columns[1:], OUTCOMES):
        column.metric(outcome.capitalize(), counts.get(outcome, 0))

    st.subheader("Outcome breakdown")
    st.bar_chart(pd.Series(counts, name="incidents"))

    st.subheader("Incident timeline")
    st.dataframe(pd.DataFrame([{
        "incident": row.incident_id,
        "fault": row.fault_kind,
        "outcome": row.outcome,
        "mitigation": row.report.mitigation_action.value if row.report.mitigation_action else None,
        "attempted": " -> ".join(row.report.attempted_actions),
        "retries": row.report.retries,
        "confidence": row.report.triage_confidence,
        "sandbox": row.report.sandbox_passed,
        "policy": row.report.policy_decision.value if row.report.policy_decision else None,
        "hitl": row.report.hitl_choice.value if row.report.hitl_choice else None,
        "cost $": round(incident_cost_usd(row.report), 6),
        "latency s": round(row.report.total_latency_ms / 1000, 1),
        "at": row.created_at,
    } for row in rows]))

    st.subheader("Open durable fixes")
    st.caption("Drafted follow-up fixes not yet promoted to their own pipeline run.")
    fixes = open_durable_fixes(rows)
    if fixes:
        st.dataframe(pd.DataFrame(fixes))
    else:
        st.caption("None open — every drafted durable fix has been promoted.")

    st.subheader("Expired approvals")
    expired = expired_approvals(rows)
    st.metric("Approval windows elapsed unanswered", len(expired))
    if expired:
        st.dataframe(pd.DataFrame([
            {"incident": row.incident_id, "fault": row.fault_kind, "at": row.created_at}
            for row in expired
        ]))

    st.subheader("Per-node cost & latency")
    stats = node_usage_stats(rows)
    if stats:
        frame = pd.DataFrame(stats).set_index("node")
        st.dataframe(frame)
        st.bar_chart(frame[["p50_latency_ms", "p95_latency_ms"]])
        st.caption(
            "Percentiles come from real incident runs. Cost prices each node's tokens at "
            "the model that produced them."
        )
    else:
        st.caption("No per-node usage recorded yet — reports written before Phase 7 have none.")


def _render_evaluation() -> None:
    st.info("Evaluation tab lands in the next task.")


def main() -> None:
    st.set_page_config(page_title="Aegis — incident response", layout="wide")
    st.title("Aegis — self-healing incident response")
    if st.button("Refresh data"):
        st.cache_data.clear()
    ops_tab, eval_tab = st.tabs(["Operations", "Evaluation"])
    with ops_tab:
        _render_operations()
    with eval_tab:
        _render_evaluation()


if __name__ == "__main__":
    main()

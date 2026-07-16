"""Report sinks: stdout for demos, Postgres for the dashboard (spec section 12)."""

from __future__ import annotations

import json

import psycopg

from agent.state import IncidentReport
from retrieval.db import conn_string

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS incident_reports (
    incident_id TEXT PRIMARY KEY,
    fault_kind TEXT NOT NULL,
    outcome TEXT NOT NULL,
    report JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

UPSERT_SQL = """
INSERT INTO incident_reports (incident_id, fault_kind, outcome, report)
VALUES (%s, %s, %s, %s)
ON CONFLICT (incident_id) DO UPDATE
SET fault_kind = EXCLUDED.fault_kind,
    outcome = EXCLUDED.outcome,
    report = EXCLUDED.report,
    created_at = now();
"""


class PrintSink:
    def emit(self, report: IncidentReport) -> None:
        print(f"\n{'=' * 70}")
        print(f"REPORT: {report.incident_id} -> {report.outcome.value}")
        print(f"  mitigation={report.mitigation_action.value if report.mitigation_action else None}"
              f" attempted={report.attempted_actions}")
        print(f"  retries={report.retries} sandbox_passed={report.sandbox_passed}")
        print(f"  policy={report.policy_decision} hitl={report.hitl_choice}")
        if report.apply_error:
            print(f"  apply_error: {report.apply_error}")
        if report.durable_fix:
            print(f"  durable fix filed: {report.durable_fix.action.value}"
                  f" -> {report.durable_fix.target_file}")
        print(f"  tokens: in={report.total_input_tokens} out={report.total_output_tokens}")
        print(f"  latency: {report.total_latency_ms:.0f}ms")
        print("=" * 70)


class PostgresReportSink:
    def __init__(self, conn_str: str | None = None):
        self._conn_str = conn_str or conn_string()
        with psycopg.connect(self._conn_str, connect_timeout=5) as conn:
            conn.execute(SCHEMA_SQL)
            conn.commit()

    def emit(self, report: IncidentReport) -> None:
        with psycopg.connect(self._conn_str, connect_timeout=5) as conn:
            conn.execute(UPSERT_SQL, (
                report.incident_id, report.fault_kind.value, report.outcome.value,
                json.dumps(report.model_dump(mode="json")),
            ))
            conn.commit()

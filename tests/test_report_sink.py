"""Report sink tests: PrintSink (stdout) and PostgresReportSink (dashboard persistence)."""

import pytest

from agent.actions import ActionType
from agent.report_sink import PostgresReportSink, PrintSink
from agent.state import IncidentReport, Outcome
from stream.schema import FaultKind


def _report(outcome=Outcome.applied, incident_id="inc-sink-1") -> IncidentReport:
    return IncidentReport(
        incident_id=incident_id,
        fault_kind=FaultKind.memory_leak,
        outcome=outcome,
        mitigation_action=ActionType.restart_service,
        attempted_actions=["restart_service"],
    )


def test_print_sink_prints_outcome_and_mitigation(capsys):
    PrintSink().emit(_report())
    out = capsys.readouterr().out
    assert "inc-sink-1" in out
    assert "applied" in out
    assert "restart_service" in out


@pytest.mark.docker
def test_postgres_sink_upsert_is_idempotent_on_outcome():
    try:
        sink = PostgresReportSink()
    except Exception:
        pytest.skip("postgres unavailable")

    import psycopg

    from retrieval.db import conn_string

    incident_id = "inc-sink-pg-1"
    sink.emit(_report(outcome=Outcome.applied, incident_id=incident_id))
    sink.emit(_report(outcome=Outcome.rejected, incident_id=incident_id))

    with psycopg.connect(conn_string()) as conn:
        rows = conn.execute(
            "SELECT outcome FROM incident_reports WHERE incident_id = %s", (incident_id,)
        ).fetchall()
    assert rows == [("rejected",)]

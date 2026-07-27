"""Report sink tests: PrintSink (stdout) and PostgresReportSink (dashboard persistence)."""

import pytest

from agent.actions import ActionType
from agent.report_sink import PostgresReportSink, PrintSink
from agent.state import IncidentReport, NodeUsage, Outcome
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


@pytest.mark.docker
def test_postgres_sink_round_trips_node_usage():
    try:
        sink = PostgresReportSink()
    except Exception:
        pytest.skip("postgres unavailable")

    import json

    import psycopg

    from observability.pricing import total_cost_usd
    from retrieval.db import conn_string

    incident_id = "inc-sink-pg-2"
    node_usage = [
        NodeUsage(
            node="triage",
            model="claude-haiku-4-5-20251001",
            input_tokens=1_000_000,
            output_tokens=0,
            latency_ms=100.0,
        ),
        NodeUsage(
            node="propose",
            model="claude-sonnet-4-6",
            input_tokens=1_000_000,
            output_tokens=0,
            latency_ms=200.0,
        ),
    ]
    report = _report(incident_id=incident_id)
    report.node_usage = node_usage
    sink.emit(report)

    with psycopg.connect(conn_string()) as conn:
        rows = conn.execute(
            "SELECT report FROM incident_reports WHERE incident_id = %s", (incident_id,)
        ).fetchall()
    assert len(rows) == 1
    report_json = rows[0][0]
    # psycopg3 decodes JSONB to dict; tolerate a text column too.
    payload = report_json if isinstance(report_json, dict) else json.loads(report_json)
    round_tripped = IncidentReport.model_validate(payload)

    assert [u.node for u in round_tripped.node_usage] == ["triage", "propose"]
    assert [u.model for u in round_tripped.node_usage] == [
        "claude-haiku-4-5-20251001", "claude-sonnet-4-6",
    ]
    assert [u.input_tokens for u in round_tripped.node_usage] == [1_000_000, 1_000_000]
    assert [u.output_tokens for u in round_tripped.node_usage] == [0, 0]

    assert total_cost_usd(round_tripped.node_usage) == pytest.approx(4.00)

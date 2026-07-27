"""Operations-tab data derivations (spec section 12): outcomes, durable-fix queue, per-node cost."""

from datetime import datetime, timezone

import pytest

from agent.actions import ActionType, ProposedAction
from agent.state import HitlChoice, IncidentReport, NodeUsage, Outcome
from dashboard.operations_data import (
    IncidentRow,
    _percentile,
    expired_approvals,
    incident_cost_usd,
    node_usage_stats,
    open_durable_fixes,
    outcome_breakdown,
)
from stream.schema import FaultKind

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"


def _patch_fix() -> ProposedAction:
    return ProposedAction(
        action=ActionType.patch_code, reason="cap the buffer",
        patch="--- a\n+++ b\n", target_file="mock_app/faults/memory_leak.py",
    )


def _row(incident_id, *, outcome=Outcome.applied, durable_fix=None,
         node_usage=(), hitl=None, minute=0) -> IncidentRow:
    report = IncidentReport(
        incident_id=incident_id,
        fault_kind=FaultKind.memory_leak,
        outcome=outcome,
        mitigation_action=ActionType.restart_service,
        durable_fix=durable_fix,
        hitl_choice=hitl,
        node_usage=list(node_usage),
    )
    return IncidentRow(
        incident_id=incident_id,
        fault_kind=FaultKind.memory_leak.value,
        outcome=outcome.value,
        created_at=datetime(2026, 7, 26, 12, minute, tzinfo=timezone.utc),
        report=report,
    )


def test_outcome_breakdown_counts_each_outcome():
    rows = [_row("a"), _row("b"), _row("c", outcome=Outcome.blocked)]
    assert outcome_breakdown(rows) == {"applied": 2, "blocked": 1}


def test_open_durable_fixes_excludes_promoted_and_fixless_incidents():
    rows = [
        _row("inc-open", durable_fix=_patch_fix()),          # open: drafted, never promoted
        _row("inc-done", durable_fix=_patch_fix()),          # resolved: has a #promo run below
        _row("inc-done#promo", durable_fix=_patch_fix()),
        _row("inc-none"),                                    # no durable fix drafted
    ]
    assert [f["incident_id"] for f in open_durable_fixes(rows)] == ["inc-open"]
    entry = open_durable_fixes(rows)[0]
    assert entry["action"] == "patch_code"
    assert entry["target_file"] == "mock_app/faults/memory_leak.py"


def test_open_durable_fixes_treats_doubly_promoted_run_as_resolving_the_base():
    # A promoted run that is itself promoted persists as "<id>#promo#promo".
    rows = [_row("inc-1", durable_fix=_patch_fix()), _row("inc-1#promo#promo")]
    assert open_durable_fixes(rows) == []


def test_open_durable_fixes_is_filed_for_non_applied_outcomes_too():
    # A blocked incident still has an unfixed root cause worth ticketing.
    rows = [_row("inc-blocked", outcome=Outcome.blocked, durable_fix=_patch_fix())]
    assert [f["incident_id"] for f in open_durable_fixes(rows)] == ["inc-blocked"]


def test_expired_approvals_selects_only_expired_hitl_rows():
    rows = [_row("a", hitl=HitlChoice.expired), _row("b", hitl=HitlChoice.approve), _row("c")]
    assert [r.incident_id for r in expired_approvals(rows)] == ["a"]


def test_incident_cost_prices_each_node_at_its_own_model_rate():
    report = _row("a", node_usage=[
        NodeUsage(node="triage", model=HAIKU, input_tokens=1_000_000, output_tokens=0, latency_ms=1.0),
        NodeUsage(node="propose", model=SONNET, input_tokens=1_000_000, output_tokens=0, latency_ms=1.0),
    ]).report
    assert incident_cost_usd(report) == pytest.approx(4.00)


def test_incident_cost_is_zero_for_legacy_rows_without_node_usage():
    assert incident_cost_usd(_row("legacy").report) == 0.0


@pytest.mark.parametrize("values,pct,expected", [
    ([42.0], 50, 42.0),          # single sample: percentile is the sample
    ([42.0], 95, 42.0),
    ([10.0, 20.0, 30.0], 50, 20.0),
    ([10.0, 20.0, 30.0], 95, 29.0),   # rank 1.9 -> 20 + (30-20)*0.9
    ([10.0, 20.0, 30.0], 0, 10.0),
    ([30.0, 10.0, 20.0], 50, 20.0),   # unsorted input
])
def test_percentile_interpolates_and_survives_single_sample(values, pct, expected):
    assert _percentile(values, pct) == pytest.approx(expected)


def test_node_usage_stats_aggregates_across_incidents_sorted_by_cost():
    usage = [
        NodeUsage(node="triage", model=HAIKU, input_tokens=1_000_000, output_tokens=0, latency_ms=100.0),
        NodeUsage(node="propose", model=SONNET, input_tokens=1_000_000, output_tokens=0, latency_ms=900.0),
    ]
    stats = node_usage_stats([_row("a", node_usage=usage), _row("b", node_usage=usage)])
    assert [s["node"] for s in stats] == ["propose", "triage"]     # costliest first
    propose, triage = stats
    assert propose["calls"] == 2
    assert propose["total_cost_usd"] == pytest.approx(6.00)        # 2 x $3
    assert propose["p50_latency_ms"] == pytest.approx(900.0)
    assert triage["total_cost_usd"] == pytest.approx(2.00)         # 2 x $1


def test_node_usage_stats_is_empty_without_usage():
    assert node_usage_stats([_row("legacy")]) == []

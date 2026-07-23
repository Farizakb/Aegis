# tests/test_eval_fixtures.py
import pytest

from agent.actions import ActionType
from evals.fixtures import load_cases
from stream.schema import FaultKind

REQUIRED_TRUTH = {
    "fault_kind", "acceptable_mitigations", "expected_durable_fix",
    "expected_policy", "relevant_docs", "root_cause_reference", "retrieval_root_cause",
}
VALID_ACTIONS = {a.value for a in ActionType}


def test_corpus_has_expected_distribution():
    cases = load_cases()
    assert len(cases) == 18
    kinds = [c.incident.fault_kind for c in cases]
    assert kinds.count(FaultKind.traffic_surge) == 5
    assert kinds.count(FaultKind.error_spike) == 4


def test_every_case_parses_and_is_well_formed():
    for case in load_cases():
        assert case.incident.incident_id                       # valid IncidentEvent
        truth = case.truth
        assert REQUIRED_TRUTH <= truth.keys(), f"{case.id} missing truth keys"
        assert truth["fault_kind"] == case.incident.fault_kind.value
        assert truth["acceptable_mitigations"], f"{case.id} has empty acceptable set"
        for a in truth["acceptable_mitigations"]:
            assert a in VALID_ACTIONS
        assert truth["expected_policy"] in {"allow", "needs_approval", "block"}
        for d in truth["relevant_docs"]:
            assert d.startswith("runbook:"), f"{case.id}: only runbook labels are scored"


def test_traffic_surge_is_scale_out_only():
    for case in load_cases():
        if case.incident.fault_kind == FaultKind.traffic_surge:
            assert case.truth["acceptable_mitigations"] == ["scale_out"]
            assert case.truth["expected_durable_fix"] is None

# tests/test_eval_metrics.py
from evals.metrics import (
    action_selection_accuracy, mean_confidence, pct_below_confidence_floor,
    remediation_success_rate, retrieval_precision_at_k, retrieval_recall_at_k,
    triage_accuracy,
)


def test_triage_accuracy_counts_fault_kind_matches():
    results = [
        {"fault_kind_expected": "memory_leak", "fault_kind_actual": "memory_leak"},
        {"fault_kind_expected": "db_deadlock", "fault_kind_actual": "memory_leak"},
    ]
    assert triage_accuracy(results) == 0.5


def test_action_selection_uses_set_membership():
    results = [
        {"mitigation_actual": "restart_service", "acceptable_mitigations": ["restart_service"]},
        {"mitigation_actual": "toggle_feature_flag", "acceptable_mitigations": ["toggle_feature_flag", "rollback"]},
        {"mitigation_actual": "restart_service", "acceptable_mitigations": ["scale_out"]},  # miss
    ]
    assert action_selection_accuracy(results) == 2 / 3


def test_retrieval_precision_and_recall_source_level():
    results = [{
        "retrieved_sources": ["runbook:memory_leak.md", "commit:abc", "runbook:db_deadlock.md"],
        "relevant_docs": ["runbook:memory_leak.md"],
    }]
    # 1 relevant retrieved among top-3 -> precision 1/3, recall 1/1
    assert retrieval_precision_at_k(results, 3) == 1 / 3
    assert retrieval_recall_at_k(results, 3) == 1.0


def test_retrieval_dedupes_sources_before_scoring():
    results = [{
        "retrieved_sources": ["runbook:memory_leak.md", "runbook:memory_leak.md", "commit:x"],
        "relevant_docs": ["runbook:memory_leak.md"],
    }]
    assert retrieval_recall_at_k(results, 3) == 1.0


def test_remediation_success_rate():
    results = [{"sandbox_passed": True}, {"sandbox_passed": False}, {"sandbox_passed": True}]
    assert remediation_success_rate(results) == 2 / 3


def test_confidence_diagnostics():
    results = [
        {"fault_kind_expected": "memory_leak", "fault_kind_actual": "memory_leak", "confidence": 0.9},
        {"fault_kind_expected": "memory_leak", "fault_kind_actual": "memory_leak", "confidence": 0.5},
        {"fault_kind_expected": "db_deadlock", "fault_kind_actual": "memory_leak", "confidence": 0.4},  # wrong
    ]
    assert mean_confidence(results) == 0.7            # mean over the 2 correct only
    assert pct_below_confidence_floor(results, 0.7) == 2 / 3

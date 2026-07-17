"""Per-rule unit tests for the 7-rule deny-by-default policy engine (ADR-0004)."""

import pytest

from agent.actions import ActionType, ProposedAction
from agent.state import PolicyDecision, SandboxResult, TriageResult
from policy.engine import PolicyEngine
from stream.schema import FaultKind


def _engine(now=1000.0):
    return PolicyEngine(clock=lambda: now)


def _triage(confidence=0.9):
    return TriageResult(
        fault_kind=FaultKind.memory_leak, root_cause="leak",
        confidence=confidence, reasoning="test",
    )


def _sandbox(passed=True):
    return SandboxResult(
        passed=passed, exit_code=0 if passed else 1, stdout="", stderr="",
        duration_ms=1.0, before_metrics={"rss_mb": 80.0},
        after_metrics={"rss_mb": 0.0},
    )


def _restart():
    return ProposedAction(action=ActionType.restart_service, reason="leak")


# Rule 1 — deny-by-default
def test_unknown_action_type_is_blocked():
    verdict = _engine().evaluate(
        proposal={"action": "delete_database", "reason": "??"},
        sandbox=None, triage=_triage(),
    )
    assert verdict.decision is PolicyDecision.block
    assert verdict.violated_rules == ["deny_by_default"]


def test_malformed_proposal_missing_params_is_blocked():
    # patch_code without patch/target_file fails catalog validation -> block
    verdict = _engine().evaluate(
        proposal={"action": "patch_code", "reason": "fix"},
        sandbox=_sandbox(), triage=_triage(),
    )
    assert verdict.decision is PolicyDecision.block
    assert "deny_by_default" in verdict.violated_rules


def test_valid_dict_proposal_is_coerced_and_evaluated():
    verdict = _engine().evaluate(
        proposal={"action": "restart_service", "reason": "leak"},
        sandbox=_sandbox(), triage=_triage(),
    )
    assert verdict.decision is PolicyDecision.allow


# Escalate — deliberate safe terminal (ledger carry-in)
def test_escalate_is_allowed_without_sandbox_proof():
    verdict = _engine().evaluate(
        proposal=ProposedAction(action=ActionType.escalate, reason="stuck"),
        sandbox=None, triage=None,
    )
    assert verdict.decision is PolicyDecision.allow
    assert verdict.violated_rules == []
    assert verdict.reasons == ["escalate: terminal hand-off to a human"]


# Rule 2 — sandbox proof required
def test_no_sandbox_result_caps_at_needs_approval():
    verdict = _engine().evaluate(proposal=_restart(), sandbox=None, triage=_triage())
    assert verdict.decision is PolicyDecision.needs_approval
    assert "sandbox_proof_required" in verdict.violated_rules


def test_failed_sandbox_caps_at_needs_approval():
    verdict = _engine().evaluate(
        proposal=_restart(), sandbox=_sandbox(passed=False), triage=_triage(),
    )
    assert verdict.decision is PolicyDecision.needs_approval
    assert "sandbox_proof_required" in verdict.violated_rules


def test_passing_sandbox_high_confidence_restart_is_allowed():
    verdict = _engine().evaluate(proposal=_restart(), sandbox=_sandbox(), triage=_triage())
    assert verdict.decision is PolicyDecision.allow
    assert verdict.violated_rules == []


# Rule 3 — irreversibility gate
def test_patch_code_always_needs_approval_even_with_proof():
    proposal = ProposedAction(
        action=ActionType.patch_code, reason="fix",
        patch="--- a\n+++ b\n", target_file="mock_app/faults/memory_leak.py",
    )
    verdict = _engine().evaluate(proposal=proposal, sandbox=_sandbox(), triage=_triage())
    assert verdict.decision is PolicyDecision.needs_approval
    assert "irreversibility_gate" in verdict.violated_rules


# Rule 4 — confidence floor
def test_low_confidence_needs_approval():
    verdict = _engine().evaluate(
        proposal=_restart(), sandbox=_sandbox(), triage=_triage(confidence=0.5),
    )
    assert verdict.decision is PolicyDecision.needs_approval
    assert "confidence_floor" in verdict.violated_rules


def test_missing_triage_is_treated_as_low_confidence():
    verdict = _engine().evaluate(proposal=_restart(), sandbox=_sandbox(), triage=None)
    assert verdict.decision is PolicyDecision.needs_approval
    assert "confidence_floor" in verdict.violated_rules


# Rule 7 — patch rules
def _patch(target_file, lines=3):
    return ProposedAction(
        action=ActionType.patch_code, reason="fix",
        patch="x\n" * lines, target_file=target_file,
    )


def test_patch_outside_allowlist_needs_approval():
    verdict = _engine().evaluate(proposal=_patch("tests/conftest.py"),
                                 sandbox=_sandbox(), triage=_triage())
    assert verdict.decision is PolicyDecision.needs_approval
    assert "patch_path_allowlist" in verdict.violated_rules


def test_patch_to_protected_path_is_blocked():
    verdict = _engine().evaluate(proposal=_patch("policy/engine.py"),
                                 sandbox=_sandbox(), triage=_triage())
    assert verdict.decision is PolicyDecision.block
    assert "patch_protected_path" in verdict.violated_rules


def test_oversized_patch_needs_approval():
    verdict = _engine().evaluate(proposal=_patch("mock_app/main.py", lines=100),
                                 sandbox=_sandbox(), triage=_triage())
    assert "patch_size_cap" in verdict.violated_rules


def test_patch_traversal_to_protected_path_is_blocked():
    verdict = _engine().evaluate(proposal=_patch("mock_app/../policy/engine.py"),
                                 sandbox=_sandbox(), triage=_triage())
    assert verdict.decision is PolicyDecision.block
    assert "patch_protected_path" in verdict.violated_rules


def test_patch_with_backslash_separators_is_blocked():
    verdict = _engine().evaluate(proposal=_patch("mock_app\\..\\policy\\engine.py"),
                                 sandbox=_sandbox(), triage=_triage())
    assert verdict.decision is PolicyDecision.block


# Boundary values — pin deliberate comparison operators against silent flips
def test_confidence_exactly_at_floor_is_not_a_violation():
    verdict = _engine().evaluate(proposal=_restart(), sandbox=_sandbox(), triage=_triage(confidence=0.7))
    assert "confidence_floor" not in verdict.violated_rules
    assert verdict.decision is PolicyDecision.allow


def test_patch_exactly_at_size_cap_is_not_a_violation():
    verdict = _engine().evaluate(proposal=_patch("mock_app/main.py", lines=80),
                                 sandbox=_sandbox(), triage=_triage())
    assert "patch_size_cap" not in verdict.violated_rules


def test_flap_applies_at_exactly_window_edge_are_still_kept():
    # applies at t=1000, evaluate at t=1600.0 -> cutoff=1000.0; `1000 < 1000`
    # is False so the apply is NOT pruned -> still counts -> block.
    now = {"t": 1000.0}
    engine = PolicyEngine(clock=lambda: now["t"])
    for _ in range(2):
        engine.record_apply(_restart(), PolicyDecision.allow)
    now["t"] = 1600.0
    verdict = engine.evaluate(proposal=_restart(), sandbox=_sandbox(), triage=_triage())
    assert verdict.decision is PolicyDecision.block
    assert "flap_protection" in verdict.violated_rules


def test_flap_applies_just_past_window_edge_are_pruned():
    now = {"t": 1000.0}
    engine = PolicyEngine(clock=lambda: now["t"])
    for _ in range(2):
        engine.record_apply(_restart(), PolicyDecision.allow)
    now["t"] = 1600.1
    verdict = engine.evaluate(proposal=_restart(), sandbox=_sandbox(), triage=_triage())
    assert verdict.decision is PolicyDecision.allow


# Aggregation — all violations reported, worst decision wins
def test_all_violated_rules_are_listed():
    verdict = _engine().evaluate(
        proposal=_patch("policy/engine.py", lines=100), sandbox=None, triage=_triage(0.2),
    )
    assert verdict.decision is PolicyDecision.block
    assert set(verdict.violated_rules) >= {
        "sandbox_proof_required", "irreversibility_gate", "confidence_floor",
        "patch_protected_path", "patch_size_cap",
    }
    assert len(verdict.reasons) == len(verdict.violated_rules)


# Rule 5 — flap protection (stateful)
def test_third_restart_within_cooldown_is_blocked():
    now = {"t": 1000.0}
    engine = PolicyEngine(clock=lambda: now["t"])
    for _ in range(2):
        engine.record_apply(_restart(), PolicyDecision.allow)
        now["t"] += 60.0
    verdict = engine.evaluate(proposal=_restart(), sandbox=_sandbox(), triage=_triage())
    assert verdict.decision is PolicyDecision.block
    assert "flap_protection" in verdict.violated_rules


def test_flap_window_expires():
    now = {"t": 1000.0}
    engine = PolicyEngine(clock=lambda: now["t"])
    for _ in range(2):
        engine.record_apply(_restart(), PolicyDecision.allow)
    now["t"] += 601.0  # both applies age out of the 600s window
    verdict = engine.evaluate(proposal=_restart(), sandbox=_sandbox(), triage=_triage())
    assert verdict.decision is PolicyDecision.allow


def test_flap_is_per_action_and_target():
    now = {"t": 1000.0}
    engine = PolicyEngine(clock=lambda: now["t"])
    for _ in range(2):
        engine.record_apply(_restart(), PolicyDecision.allow)
    scale = ProposedAction(action=ActionType.scale_out, reason="surge", workers=8)
    verdict = engine.evaluate(proposal=scale, sandbox=_sandbox(), triage=_triage())
    assert verdict.decision is PolicyDecision.allow  # different action, same target


def test_flap_counts_applies_regardless_of_decision():
    now = {"t": 1000.0}
    engine = PolicyEngine(clock=lambda: now["t"])
    engine.record_apply(_restart(), PolicyDecision.needs_approval)
    now["t"] += 60.0
    engine.record_apply(_restart(), PolicyDecision.allow)
    now["t"] += 60.0
    verdict = engine.evaluate(proposal=_restart(), sandbox=_sandbox(), triage=_triage())
    assert verdict.decision is PolicyDecision.block
    assert "flap_protection" in verdict.violated_rules


# Rule 6 — rate-limit circuit (stateful, system-wide)
def test_circuit_opens_after_max_auto_applies():
    now = {"t": 1000.0}
    engine = PolicyEngine(clock=lambda: now["t"])
    actions = [ActionType.restart_service, ActionType.scale_out, ActionType.toggle_feature_flag]
    for i in range(5):
        a = actions[i % 3]
        kwargs = {"workers": 4} if a is ActionType.scale_out else {}
        kwargs |= {"flag_name": "risky"} if a is ActionType.toggle_feature_flag else {}
        engine.record_apply(ProposedAction(action=a, reason="x", target=f"svc-{i}", **kwargs),
                            PolicyDecision.allow)
        now["t"] += 10.0
    verdict = engine.evaluate(proposal=_restart(), sandbox=_sandbox(), triage=_triage())
    assert verdict.decision is PolicyDecision.needs_approval
    assert "rate_limit" in verdict.violated_rules


def test_needs_approval_applies_do_not_count_toward_circuit():
    now = {"t": 1000.0}
    engine = PolicyEngine(clock=lambda: now["t"])
    scale = ProposedAction(action=ActionType.scale_out, reason="surge", workers=8)
    for i in range(5):
        engine.record_apply(
            ProposedAction(action=ActionType.scale_out, reason="x", target=f"svc-{i}", workers=4),
            PolicyDecision.needs_approval,
        )
    verdict = engine.evaluate(proposal=scale, sandbox=_sandbox(), triage=_triage())
    assert verdict.decision is PolicyDecision.allow


# Rule 1 (cont.) — deny-by-default also covers unknown targets
def test_unknown_target_blocks_under_deny_by_default():
    engine = PolicyEngine()
    verdict = engine.evaluate(
        proposal=ProposedAction(action=ActionType.restart_service,
                                reason="restart the prod db", target="prod-database"),
        sandbox=_sandbox(),
        triage=_triage(),
    )
    assert verdict.decision is PolicyDecision.block
    assert verdict.violated_rules == ["deny_by_default"]


def test_escalate_with_unknown_target_also_blocks():
    # target check precedes the escalate short-circuit on purpose
    engine = PolicyEngine()
    verdict = engine.evaluate(
        proposal=ProposedAction(action=ActionType.escalate, reason="help",
                                target="prod-database"),
        sandbox=None, triage=None,
    )
    assert verdict.decision is PolicyDecision.block


def test_known_target_unaffected():
    engine = PolicyEngine()
    verdict = engine.evaluate(
        proposal=ProposedAction(action=ActionType.restart_service, reason="leak",
                                target="mock_app"),
        sandbox=_sandbox(), triage=_triage(),
    )
    assert verdict.decision is PolicyDecision.allow

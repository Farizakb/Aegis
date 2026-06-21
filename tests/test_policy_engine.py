import pytest

from agent.state import PolicyDecision, ProposedFix
from policy.engine import PolicyEngine


@pytest.fixture
def engine():
    return PolicyEngine()


def _fix(target_file: str, patch_lines: int = 5) -> ProposedFix:
    patch = "\n".join([f"+line{i}" for i in range(patch_lines)])
    return ProposedFix(description="fix", patch=patch, target_file=target_file)


def test_allow_when_target_in_mock_app(engine):
    verdict = engine.evaluate(fix=_fix("mock_app/faults/memory_leak.py"), triage=None, incident=None)
    assert verdict.decision == PolicyDecision.allow
    assert verdict.violated_rules == []


def test_needs_approval_when_target_outside_mock_app(engine):
    verdict = engine.evaluate(fix=_fix("tests/conftest.py"), triage=None, incident=None)
    assert verdict.decision == PolicyDecision.needs_approval
    assert "blast_radius" in verdict.violated_rules


def test_block_when_target_is_protected(engine):
    verdict = engine.evaluate(fix=_fix("agent/graph.py"), triage=None, incident=None)
    assert verdict.decision == PolicyDecision.block
    assert "protected_path" in verdict.violated_rules


def test_needs_approval_when_patch_too_large(engine):
    verdict = engine.evaluate(fix=_fix("mock_app/main.py", patch_lines=100), triage=None, incident=None)
    assert verdict.decision == PolicyDecision.needs_approval
    assert "patch_size" in verdict.violated_rules


def test_block_takes_precedence_over_needs_approval(engine):
    verdict = engine.evaluate(fix=_fix("policy/engine.py", patch_lines=100), triage=None, incident=None)
    assert verdict.decision == PolicyDecision.block

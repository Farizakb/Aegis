import pytest
from pydantic import ValidationError

from agent.actions import CATALOG, ActionMetadata, ActionType, BlastRadius, ProposedAction


def test_catalog_covers_every_action_type():
    assert set(CATALOG.keys()) == set(ActionType)
    assert all(isinstance(m, ActionMetadata) for m in CATALOG.values())


def test_patch_code_is_irreversible_and_needs_approval():
    meta = CATALOG[ActionType.patch_code]
    assert meta.reversible is False
    assert meta.requires_approval is True
    assert meta.blast_radius == BlastRadius.high


def test_mitigations_are_reversible():
    for action in (ActionType.restart_service, ActionType.rollback,
                   ActionType.toggle_feature_flag, ActionType.scale_out):
        assert CATALOG[action].reversible is True


def test_restart_needs_no_params():
    pa = ProposedAction(action=ActionType.restart_service, reason="clear leaked memory")
    assert pa.target == "mock_app"


def test_patch_code_requires_patch_and_target_file():
    with pytest.raises(ValidationError):
        ProposedAction(action=ActionType.patch_code, reason="fix leak")
    pa = ProposedAction(action=ActionType.patch_code, reason="fix leak",
                        patch="--- a/x\n+++ b/x\n", target_file="mock_app/faults/memory_leak.py")
    assert pa.patch


def test_toggle_requires_flag_name():
    with pytest.raises(ValidationError):
        ProposedAction(action=ActionType.toggle_feature_flag, reason="kill switch")
    pa = ProposedAction(action=ActionType.toggle_feature_flag, reason="kill switch",
                        flag_name="risky_feature")
    assert pa.flag_name == "risky_feature"


def test_scale_out_requires_workers():
    with pytest.raises(ValidationError):
        ProposedAction(action=ActionType.scale_out, reason="absorb surge")
    pa = ProposedAction(action=ActionType.scale_out, reason="absorb surge", workers=4)
    assert pa.workers == 4


def test_scale_out_rejects_non_positive_workers():
    for bad in (0, -5):
        with pytest.raises(ValidationError):
            ProposedAction(action=ActionType.scale_out, reason="absorb surge", workers=bad)

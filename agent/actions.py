"""Typed remediation action catalog: the agent's entire action space (ADR-0001)."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class ActionType(str, Enum):
    restart_service = "restart_service"
    rollback = "rollback"
    toggle_feature_flag = "toggle_feature_flag"
    scale_out = "scale_out"
    patch_code = "patch_code"
    escalate = "escalate"


class BlastRadius(str, Enum):
    none = "none"
    low = "low"
    medium = "medium"
    high = "high"


class ActionMetadata(BaseModel):
    blast_radius: BlastRadius
    reversible: bool
    requires_approval: bool


CATALOG: dict[ActionType, ActionMetadata] = {
    ActionType.restart_service: ActionMetadata(
        blast_radius=BlastRadius.low, reversible=True, requires_approval=False),
    ActionType.rollback: ActionMetadata(
        blast_radius=BlastRadius.medium, reversible=True, requires_approval=False),
    ActionType.toggle_feature_flag: ActionMetadata(
        blast_radius=BlastRadius.low, reversible=True, requires_approval=False),
    ActionType.scale_out: ActionMetadata(
        blast_radius=BlastRadius.low, reversible=True, requires_approval=False),
    ActionType.patch_code: ActionMetadata(
        blast_radius=BlastRadius.high, reversible=False, requires_approval=True),
    ActionType.escalate: ActionMetadata(
        blast_radius=BlastRadius.none, reversible=True, requires_approval=False),
}


class ProposedAction(BaseModel):
    action: ActionType
    reason: str
    target: str = "mock_app"
    patch: str | None = None
    target_file: str | None = None
    flag_name: str | None = None
    workers: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _require_action_params(self) -> "ProposedAction":
        if self.action is ActionType.patch_code and not (self.patch and self.target_file):
            raise ValueError("patch_code requires patch and target_file")
        if self.action is ActionType.toggle_feature_flag and not self.flag_name:
            raise ValueError("toggle_feature_flag requires flag_name")
        if self.action is ActionType.scale_out and self.workers is None:
            raise ValueError("scale_out requires workers")
        return self

"""Agent state contract: typed models for all graph nodes."""

from __future__ import annotations

from enum import Enum
from typing import TypedDict

from pydantic import BaseModel, Field

from agent.actions import ActionType, ProposedAction
from retrieval.models import RetrievedChunk
from stream.schema import FaultKind, IncidentEvent


class TriageResult(BaseModel):
    fault_kind: FaultKind
    root_cause: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class RemediationPlan(BaseModel):
    """Mitigate-first plan: mitigation executes now, durable_fix is filed as follow-up (ADR-0003)."""

    mitigation: ProposedAction
    durable_fix: ProposedAction | None = None


class NodeUsage(BaseModel):
    node: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class SandboxResult(BaseModel):
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    # Phase 2 (ADR-0002): empirical fault-replay evidence for the HITL brief
    before_metrics: dict[str, float] | None = None
    after_metrics: dict[str, float] | None = None
    failure_reason: str | None = None


class PolicyDecision(str, Enum):
    allow = "allow"
    needs_approval = "needs_approval"
    block = "block"


class PolicyVerdict(BaseModel):
    decision: PolicyDecision
    violated_rules: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class AttemptRecord(BaseModel):
    """One sandbox-verified attempt: what was tried, what the replay proved (ADR-0006)."""

    action: ProposedAction
    result: SandboxResult


class HitlChoice(str, Enum):
    approve = "approve"
    reject = "reject"
    expired = "expired"


class HitlDecision(BaseModel):
    choice: HitlChoice
    decided_by: str = "local-ui"
    note: str | None = None


class Outcome(str, Enum):
    applied = "applied"
    rejected = "rejected"
    blocked = "blocked"
    escalated = "escalated"


class IncidentReport(BaseModel):
    incident_id: str
    fault_kind: FaultKind
    outcome: Outcome
    mitigation_action: ActionType | None = None
    attempted_actions: list[str] = Field(default_factory=list)
    durable_fix: ProposedAction | None = None
    triage_confidence: float | None = None
    retries: int = 0
    sandbox_passed: bool | None = None
    policy_decision: PolicyDecision | None = None
    hitl_choice: HitlChoice | None = None
    apply_error: str | None = None
    applied_target_file: str | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_latency_ms: float = 0.0
    # Per-node usage retained so the dashboard can price each node's tokens against
    # the model that produced them (aggregate tokens alone are not priceable).
    node_usage: list[NodeUsage] = Field(default_factory=list)


class AgentState(TypedDict):
    incident: IncidentEvent
    triage: TriageResult | None
    retrieved_context: list[RetrievedChunk]
    plan: RemediationPlan | None
    attempted: list[AttemptRecord]
    retries: int
    sandbox_result: SandboxResult | None
    policy_verdict: PolicyVerdict | None
    hitl_decision: HitlDecision | None
    applied: bool
    apply_error: str | None
    report: IncidentReport | None
    usage: list[NodeUsage]


def initial_state(incident: IncidentEvent, *, plan: RemediationPlan | None = None,
                  triage: TriageResult | None = None) -> AgentState:
    """Seed a graph run. Promoted durable-fix runs preset `plan` (and reuse the
    original run's triage); route_entry then skips straight to sandbox."""
    return AgentState(
        incident=incident, triage=triage, retrieved_context=[], plan=plan,
        attempted=[], retries=0, sandbox_result=None, policy_verdict=None,
        hitl_decision=None, applied=False, apply_error=None, report=None, usage=[],
    )

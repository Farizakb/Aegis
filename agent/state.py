"""Agent state contract: typed models for all graph nodes."""

from __future__ import annotations

from enum import Enum
from typing import TypedDict

from pydantic import BaseModel, Field

from agent.actions import ProposedAction
from retrieval.models import RetrievedChunk
from stream.schema import FaultKind, IncidentEvent


class TriageResult(BaseModel):
    fault_kind: FaultKind
    root_cause: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class ProposedFix(BaseModel):
    description: str
    patch: str
    target_file: str | None = None


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


class PolicyDecision(str, Enum):
    allow = "allow"
    needs_approval = "needs_approval"
    block = "block"


class PolicyVerdict(BaseModel):
    decision: PolicyDecision
    violated_rules: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class HitlChoice(str, Enum):
    approve = "approve"
    reject = "reject"


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
    triage_confidence: float | None = None
    retries: int = 0
    sandbox_passed: bool | None = None
    policy_decision: PolicyDecision | None = None
    hitl_choice: HitlChoice | None = None
    applied_target_file: str | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_latency_ms: float = 0.0


class AgentState(TypedDict):
    incident: IncidentEvent
    triage: TriageResult | None
    retrieved_context: list[RetrievedChunk]
    proposed_fix: ProposedFix | None
    retries: int
    sandbox_result: SandboxResult | None
    policy_verdict: PolicyVerdict | None
    hitl_decision: HitlDecision | None
    report: IncidentReport | None
    usage: list[NodeUsage]

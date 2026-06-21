"""Agent state contract: IncidentEvent in, TriageResult/RetrievedChunk/ProposedFix out."""

from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel, Field

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


class NodeUsage(BaseModel):
    node: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class AgentState(TypedDict):
    incident: IncidentEvent
    triage: TriageResult | None
    retrieved_context: list[RetrievedChunk]
    proposed_fix: ProposedFix | None
    retries: int
    sandbox_result: dict | None
    policy_verdict: dict | None
    hitl_decision: dict | None
    usage: list[NodeUsage]

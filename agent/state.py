"""Agent state contract: IncidentEvent in, TriageResult/RetrievedChunk/ProposedFix out."""

from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel, Field

from stream.schema import FaultKind, IncidentEvent


class TriageResult(BaseModel):
    fault_kind: FaultKind
    root_cause: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class RetrievedChunk(BaseModel):
    source: str
    content: str
    score: float


class ProposedFix(BaseModel):
    description: str
    patch: str
    target_file: str | None = None


class AgentState(TypedDict):
    incident: IncidentEvent
    triage: TriageResult | None
    retrieved_context: list[RetrievedChunk]
    proposed_fix: ProposedFix | None

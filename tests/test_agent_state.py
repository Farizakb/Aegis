from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent.state import ProposedFix, RetrievedChunk, TriageResult
from stream.schema import FaultKind, IncidentEvent, Severity


def make_incident() -> IncidentEvent:
    now = datetime.now(timezone.utc)
    return IncidentEvent(
        incident_id="inc-1",
        fault_kind=FaultKind.memory_leak,
        severity=Severity.critical,
        source="mock_app",
        title="memory_leak on mock_app",
        summary="rss climbed to 200MB",
        first_seen=now,
        last_seen=now,
        event_count=3,
        duplicate_count=5,
        sample_events=[],
        correlation_window_s=3.0,
    )


def test_triage_result_validates():
    triage = TriageResult(
        fault_kind=FaultKind.memory_leak,
        root_cause="unbounded list growth in MemoryLeakFault",
        confidence=0.9,
        reasoning="rss grows linearly with each tick",
    )
    assert triage.confidence == 0.9


def test_triage_result_rejects_out_of_range_confidence():
    with pytest.raises(ValidationError):
        TriageResult(
            fault_kind=FaultKind.memory_leak,
            root_cause="x",
            confidence=1.5,
            reasoning="x",
        )


def test_retrieved_chunk_and_proposed_fix_roundtrip():
    chunk = RetrievedChunk(source="runbook:memory_leak.md", content="...", score=0.83)
    fix = ProposedFix(
        description="cap memory growth",
        patch="--- a/mock_app/faults/memory_leak.py\n+++ b/mock_app/faults/memory_leak.py\n",
        target_file="mock_app/faults/memory_leak.py",
    )

    assert chunk.score == 0.83
    assert fix.target_file == "mock_app/faults/memory_leak.py"


def test_incident_event_is_the_agent_input():
    incident = make_incident()
    assert incident.fault_kind == FaultKind.memory_leak

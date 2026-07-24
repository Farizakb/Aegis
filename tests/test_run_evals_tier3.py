"""Tier-3 remediation replay: proposed mitigation -> Docker sandbox fault replay."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.actions import ActionType, ProposedAction
from agent.state import RemediationPlan, SandboxResult

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_tier3_metrics_shape():
    from evals.run_evals import _tier3_metrics

    results = [
        {"id": "a", "sandbox_passed": True},
        {"id": "b", "sandbox_passed": False},
        {"id": "c", "sandbox_passed": True},
    ]
    m = _tier3_metrics(results)
    assert m["remediation_success_rate"] == pytest.approx(2 / 3)


async def test_tier3_assembles_result(monkeypatch):
    from evals.fixtures import EvalCase
    from evals import run_evals
    from stream.schema import FaultKind, IncidentEvent, RawEvent, Severity

    t0 = "2026-07-23T12:00:00Z"
    incident = IncidentEvent(
        incident_id="memory_leak_01", fault_kind=FaultKind.memory_leak,
        severity=Severity.critical, source="mock_app", title="t", summary="s",
        first_seen=t0, last_seen=t0, event_count=1, duplicate_count=0,
        sample_events=[RawEvent(event_id="e1", fault_kind=FaultKind.memory_leak,
                                severity=Severity.critical, source="mock_app",
                                message="rss high", metric=800.0, ts=t0, dedup_key="k")],
        correlation_window_s=30.0,
    )
    case = EvalCase(id="memory_leak_01", incident=incident, truth={
        "fault_kind": "memory_leak", "acceptable_mitigations": ["restart_service"],
        "expected_durable_fix": None, "relevant_docs": [],
        "root_cause_reference": "buffer growth", "retrieval_root_cause": "buffer growth",
    })

    plan = RemediationPlan(mitigation=ProposedAction(action=ActionType.restart_service, reason="x"))
    result_dict = {
        "id": case.id, "fault_kind_expected": "memory_leak", "fault_kind_actual": "memory_leak",
        "confidence": 0.9, "root_cause_actual": "leak", "mitigation_actual": "restart_service",
        "durable_fix_actual": None, "retrieved_sources": [],
        "acceptable_mitigations": ["restart_service"], "relevant_docs": [],
        "root_cause_reference": "buffer growth",
    }

    async def fake_drive_propose(case_, *, triage_llm, propose_llm, search_tool):
        return result_dict, plan, []

    monkeypatch.setattr("evals.drivers.drive_propose", fake_drive_propose)

    class FakeExecutor:
        async def verify(self, action, fault_kind):
            return SandboxResult(passed=True, exit_code=0, stdout="", stderr="",
                                 duration_ms=1.0, before_metrics={"rss_mb": 80.0},
                                 after_metrics={"rss_mb": 0.0})

    results = await run_evals.run_tier3(
        [case], triage_llm=None, propose_llm=None, search_tool=None, executor=FakeExecutor(),
    )

    assert len(results) == 1
    r = results[0]
    assert r["sandbox_passed"] is True
    assert r["mitigation_actual"] == "restart_service"


def _docker_client():
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return client
    except Exception:
        return None


_CLIENT = _docker_client()


@pytest.fixture(scope="session")
def app_image():
    from sandbox.executor import APP_IMAGE

    _CLIENT.images.build(
        path=str(REPO_ROOT), dockerfile="mock_app/Dockerfile", tag=APP_IMAGE
    )
    return APP_IMAGE


@pytest.mark.docker
@pytest.mark.skipif(_CLIENT is None, reason="Docker daemon not available")
async def test_tier3_real_replay(app_image):
    from agent.llm import get_propose_llm, get_triage_llm
    from evals.drivers import DirectSearchAdapter
    from evals.fixtures import load_cases
    from evals.run_evals import run_tier3
    from retrieval.db import get_conn
    from sandbox.executor import SandboxExecutor

    cases = [c for c in load_cases() if c.incident.fault_kind.value == "memory_leak"][:1]

    results = await run_tier3(
        cases,
        triage_llm=get_triage_llm(),
        propose_llm=get_propose_llm(),
        search_tool=DirectSearchAdapter(get_conn()),
        executor=SandboxExecutor(_CLIENT),
    )

    assert results
    assert isinstance(results[0]["sandbox_passed"], bool)

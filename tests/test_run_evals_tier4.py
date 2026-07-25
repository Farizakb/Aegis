"""Tier-4 e2e smoke: fast unit tests for the pure-Python pieces + one real
end-to-end run through the whole compiled graph, Docker-gated."""
from __future__ import annotations

import pytest

from evals.run_evals import _expected_outcome, _tier4_metrics


def test_expected_outcome_mapping():
    assert _expected_outcome("block") == "blocked"
    assert _expected_outcome("needs_approval") == "applied"
    assert _expected_outcome("allow") == "applied"


def test_tier4_metrics_shape():
    results = [
        {"outcome_actual": "applied", "outcome_expected": "applied"},
        {"outcome_actual": "blocked", "outcome_expected": "applied"},
    ]
    assert _tier4_metrics(results) == {"e2e_outcome_accuracy": 0.5}
    assert _tier4_metrics([]) == {"e2e_outcome_accuracy": 1.0}


def _docker_client():
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return client
    except Exception:
        return None


_CLIENT = _docker_client()


@pytest.mark.docker
@pytest.mark.skipif(_CLIENT is None, reason="Docker daemon not available")
async def test_tier4_real_smoke():
    import os

    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    from agent.llm import get_propose_llm, get_triage_llm
    from agent.state import Outcome
    from evals.drivers import DirectSearchAdapter
    from evals.fixtures import load_cases
    from retrieval.db import get_conn
    from evals.run_evals import run_tier4
    from sandbox.executor import SandboxExecutor

    try:
        conn = get_conn()
    except Exception as exc:
        pytest.skip(f"Postgres unreachable: {exc}")

    try:
        cases = [c for c in load_cases() if c.incident.fault_kind.value == "memory_leak"][:1]

        triage_llm = get_triage_llm()
        propose_llm = get_propose_llm()
        search_tool = DirectSearchAdapter(conn)
        executor = SandboxExecutor(_CLIENT)

        results = await run_tier4(cases, triage_llm=triage_llm, propose_llm=propose_llm,
                                  search_tool=search_tool, executor=executor)

        assert len(results) == 1
        assert "outcome_actual" in results[0]
        assert results[0]["outcome_actual"] in {o.value for o in Outcome}
    finally:
        conn.close()

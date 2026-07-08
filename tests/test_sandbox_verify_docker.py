"""Empirical fault-replay verification against real containers. Requires Docker Desktop."""

from pathlib import Path

import pytest

from agent.actions import ActionType, ProposedAction
from sandbox.executor import APP_IMAGE, SandboxExecutor
from stream.schema import FaultKind

REPO_ROOT = Path(__file__).resolve().parents[1]


def _docker_client():
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return client
    except Exception:
        return None


_CLIENT = _docker_client()

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(_CLIENT is None, reason="Docker daemon not available"),
]


@pytest.fixture(scope="session")
def app_image():
    _CLIENT.images.build(
        path=str(REPO_ROOT), dockerfile="mock_app/Dockerfile", tag=APP_IMAGE
    )
    return APP_IMAGE


@pytest.fixture
def executor(app_image):
    return SandboxExecutor(_CLIENT)


RESTART = ProposedAction(action=ActionType.restart_service, reason="restart the service")


async def test_restart_clears_memory_leak(executor):
    result = await executor.verify(RESTART, FaultKind.memory_leak)
    assert result.passed is True
    assert result.before_metrics["rss_mb"] >= 15.0
    assert result.after_metrics.get("rss_mb", 0.0) <= 10.0
    assert result.failure_reason is None


async def test_restart_clears_db_deadlock(executor):
    result = await executor.verify(RESTART, FaultKind.db_deadlock)
    assert result.passed is True
    assert result.before_metrics["lock_wait_ms"] >= 500.0


async def test_restart_does_not_fix_traffic_surge(executor):
    result = await executor.verify(RESTART, FaultKind.traffic_surge)
    assert result.passed is False
    assert result.before_metrics["latency_ms"] == 200.0
    assert result.after_metrics["latency_ms"] == 200.0  # re-triggered; workers back to 2
    assert "latency_ms" in result.failure_reason


async def test_restart_does_not_fix_error_spike(executor):
    result = await executor.verify(RESTART, FaultKind.error_spike)
    assert result.passed is False
    assert result.before_metrics["error_rate_pct"] >= 30.0
    assert result.after_metrics["error_rate_pct"] > 25.0  # re-triggered: flag is on again


async def test_no_sandbox_containers_leak(executor):
    await executor.verify(RESTART, FaultKind.memory_leak)
    leftovers = _CLIENT.containers.list(all=True, filters={"label": "aegis-sandbox"})
    assert leftovers == []

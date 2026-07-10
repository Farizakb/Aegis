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


async def test_scale_out_fixes_traffic_surge(executor):
    action = ProposedAction(action=ActionType.scale_out, reason="absorb surge", workers=8)
    result = await executor.verify(action, FaultKind.traffic_surge)
    assert result.passed is True
    assert result.before_metrics["latency_ms"] == 200.0
    assert result.after_metrics["latency_ms"] == 50.0


async def test_scale_out_does_not_fix_memory_leak(executor):
    action = ProposedAction(action=ActionType.scale_out, reason="more workers", workers=8)
    result = await executor.verify(action, FaultKind.memory_leak)
    assert result.passed is False
    assert result.after_metrics["rss_mb"] > result.before_metrics["rss_mb"]  # kept leaking


async def test_flag_kill_switch_decays_error_spike(executor):
    action = ProposedAction(
        action=ActionType.toggle_feature_flag, reason="kill switch", flag_name="risky_feature"
    )
    result = await executor.verify(action, FaultKind.error_spike)
    assert result.passed is True
    assert result.before_metrics["error_rate_pct"] >= 30.0
    assert result.after_metrics["error_rate_pct"] <= 25.0  # decayed, fault still active


ROLLBACK = ProposedAction(action=ActionType.rollback, reason="revert last deploy")


async def test_rollback_fixes_db_deadlock(executor):
    result = await executor.verify(ROLLBACK, FaultKind.db_deadlock)
    assert result.passed is True
    assert result.before_metrics["lock_wait_ms"] >= 500.0
    assert result.after_metrics.get("lock_wait_ms", 0.0) <= 100.0  # bad query gone


async def test_rollback_fixes_error_spike(executor):
    result = await executor.verify(ROLLBACK, FaultKind.error_spike)
    assert result.passed is True
    assert result.after_metrics.get("error_rate_pct", 0.0) <= 25.0


async def test_rollback_does_not_fix_memory_leak(executor):
    result = await executor.verify(ROLLBACK, FaultKind.memory_leak)
    assert result.passed is False  # latent bug predates the last deploy — leak reproduces
    assert result.after_metrics["rss_mb"] > 10.0


async def test_no_containers_leak_after_rollback(executor):
    await executor.verify(ROLLBACK, FaultKind.db_deadlock)
    leftovers = _CLIENT.containers.list(all=True, filters={"label": "aegis-sandbox"})
    assert leftovers == []


# tests/fixtures/memory_leak_cap.patch caps the leak at MAX_CHUNKS = 2: after
# patch + restart + re-trigger, rss plateaus at MAX_CHUNKS * CHUNK_SIZE_MB
# (mock_app/faults/memory_leak.py) = 2 * 5.0 = 10.0 MB — exactly the 10.0
# recovery ceiling in sandbox/recovery.py (3 chunks = 15.0 would sit at the
# degraded floor and fail recovery; the before-phase floor is met by the
# UNPATCHED code, ~15 ticks x 5 MB). Change CHUNK_SIZE_MB or the 10.0/15.0
# thresholds and the fixture must be revisited.
def _patch_action(fixture: str) -> ProposedAction:
    patch = (REPO_ROOT / "tests" / "fixtures" / fixture).read_text()
    return ProposedAction(
        action=ActionType.patch_code, reason="cap the leaked buffer",
        patch=patch, target_file="mock_app/faults/memory_leak.py",
    )


@pytest.fixture(scope="session")
def sandbox_image():
    _CLIENT.images.build(path=str(REPO_ROOT), dockerfile="sandbox/Dockerfile",
                         tag="aegis-sandbox:latest")
    return "aegis-sandbox:latest"


async def test_patch_fixes_memory_leak_durably(executor, sandbox_image):
    result = await executor.verify(_patch_action("memory_leak_cap.patch"), FaultKind.memory_leak)
    assert result.passed is True
    assert result.before_metrics["rss_mb"] >= 15.0
    # re-triggered after the patched restart: the leak cannot recur
    assert result.after_metrics["rss_mb"] <= 10.0
    assert "passed" in result.stdout  # pytest gate output is part of the evidence


async def test_patch_failing_tests_is_rejected(executor, sandbox_image):
    result = await executor.verify(_patch_action("memory_leak_broken.patch"), FaultKind.memory_leak)
    assert result.passed is False
    assert result.failure_reason == "pytest gate failed"
    assert result.after_metrics is None  # never applied to the replay container

from agent.actions import ActionType
from agent.state import SandboxResult
from sandbox.recovery import check_degraded, check_recovered, should_retrigger
from stream.schema import FaultKind


def test_sandbox_result_v2_fields_default_none():
    r = SandboxResult(passed=True, exit_code=0, stdout="", stderr="", duration_ms=1.0)
    assert r.before_metrics is None
    assert r.after_metrics is None
    assert r.failure_reason is None


def test_sandbox_result_v2_carries_evidence():
    r = SandboxResult(
        passed=False, exit_code=1, stdout="", stderr="", duration_ms=1.0,
        before_metrics={"latency_ms": 200.0}, after_metrics={"latency_ms": 200.0},
        failure_reason="latency_ms 200.0 above recovery ceiling 100.0 (before: 200.0)",
    )
    assert r.before_metrics == {"latency_ms": 200.0}
    assert "ceiling" in r.failure_reason


def test_check_degraded_floors():
    assert check_degraded(FaultKind.memory_leak, {"rss_mb": 15.0}) is True
    assert check_degraded(FaultKind.memory_leak, {"rss_mb": 14.9}) is False
    assert check_degraded(FaultKind.memory_leak, {}) is False
    assert check_degraded(FaultKind.db_deadlock, {"lock_wait_ms": 500.0}) is True
    assert check_degraded(FaultKind.error_spike, {"error_rate_pct": 30.0}) is True
    assert check_degraded(FaultKind.error_spike, {"error_rate_pct": 29.0}) is False
    assert check_degraded(FaultKind.traffic_surge, {"latency_ms": 200.0}) is True
    assert check_degraded(FaultKind.traffic_surge, {"latency_ms": 149.0}) is False


def test_check_recovered_ceilings():
    ok, reason = check_recovered(FaultKind.traffic_surge, {"latency_ms": 200.0}, {"latency_ms": 50.0})
    assert ok is True and reason == ""
    ok, reason = check_recovered(FaultKind.traffic_surge, {"latency_ms": 200.0}, {"latency_ms": 200.0})
    assert ok is False
    assert reason == "latency_ms 200.0 above recovery ceiling 100.0 (before: 200.0)"
    # absent metric = fault inactive = recovered
    ok, _ = check_recovered(FaultKind.memory_leak, {"rss_mb": 70.0}, {})
    assert ok is True
    ok, _ = check_recovered(FaultKind.memory_leak, {"rss_mb": 70.0}, {"rss_mb": 10.0})
    assert ok is True
    ok, _ = check_recovered(FaultKind.memory_leak, {"rss_mb": 70.0}, {"rss_mb": 10.1})
    assert ok is False
    ok, _ = check_recovered(FaultKind.error_spike, {"error_rate_pct": 50.0}, {"error_rate_pct": 25.0})
    assert ok is True
    ok, _ = check_recovered(FaultKind.db_deadlock, {"lock_wait_ms": 1900.0}, {"lock_wait_ms": 100.0})
    assert ok is True


def test_retrigger_restart_only_for_external_condition_faults():
    assert should_retrigger(ActionType.restart_service, FaultKind.error_spike) is True
    assert should_retrigger(ActionType.restart_service, FaultKind.traffic_surge) is True
    assert should_retrigger(ActionType.restart_service, FaultKind.memory_leak) is False
    assert should_retrigger(ActionType.restart_service, FaultKind.db_deadlock) is False


def test_retrigger_always_for_durable_fixes():
    for kind in FaultKind:
        assert should_retrigger(ActionType.rollback, kind) is True
        assert should_retrigger(ActionType.patch_code, kind) is True


def test_no_retrigger_for_non_restart_mitigations():
    assert should_retrigger(ActionType.scale_out, FaultKind.traffic_surge) is False
    assert should_retrigger(ActionType.toggle_feature_flag, FaultKind.error_spike) is False

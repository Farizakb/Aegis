"""Pure recovery-assertion rules for sandbox fault replay (ADR-0002). No Docker here."""

from __future__ import annotations

from agent.actions import ActionType
from stream.schema import FaultKind

# A fault must first provably reproduce: BEFORE metrics at or past these floors.
DEGRADED_FLOOR: dict[FaultKind, tuple[str, float]] = {
    FaultKind.memory_leak: ("rss_mb", 15.0),
    FaultKind.db_deadlock: ("lock_wait_ms", 500.0),
    FaultKind.error_spike: ("error_rate_pct", 30.0),
    FaultKind.traffic_surge: ("latency_ms", 150.0),
}

# Recovery: AFTER metrics back at or under these ceilings (absent metric = fault inactive = 0.0).
RECOVERED_CEILING: dict[FaultKind, tuple[str, float]] = {
    FaultKind.memory_leak: ("rss_mb", 10.0),
    FaultKind.db_deadlock: ("lock_wait_ms", 100.0),
    FaultKind.error_spike: ("error_rate_pct", 25.0),
    FaultKind.traffic_surge: ("latency_ms", 100.0),
}

# Restart-class actions reset in-process state; the fault's root condition survives the
# restart when it lives outside the process (live load, a bad feature still deployed and
# flagged on). Durable fixes must prove the fault cannot recur, so they always face a
# re-trigger.
_EXTERNAL_CONDITION: frozenset[FaultKind] = frozenset(
    {FaultKind.error_spike, FaultKind.traffic_surge}
)
_DURABLE_FIX: frozenset[ActionType] = frozenset(
    {ActionType.rollback, ActionType.patch_code}
)


def check_degraded(kind: FaultKind, metrics: dict[str, float]) -> bool:
    name, floor = DEGRADED_FLOOR[kind]
    return metrics.get(name, 0.0) >= floor


def check_recovered(
    kind: FaultKind, before: dict[str, float], after: dict[str, float]
) -> tuple[bool, str]:
    name, ceiling = RECOVERED_CEILING[kind]
    value = after.get(name, 0.0)
    if value <= ceiling:
        return True, ""
    return False, (
        f"{name} {value} above recovery ceiling {ceiling} "
        f"(before: {before.get(name, 0.0)})"
    )


def should_retrigger(action: ActionType, kind: FaultKind) -> bool:
    if action in _DURABLE_FIX:
        return True
    if action is ActionType.restart_service:
        return kind in _EXTERNAL_CONDITION
    return False

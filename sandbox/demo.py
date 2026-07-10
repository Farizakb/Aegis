"""Phase-2 demo CLI: inject a fault, apply an action, print verified before/after evidence.

Usage:
    python -m sandbox.demo memory_leak restart_service
    python -m sandbox.demo --matrix
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import docker

from agent.actions import ActionType, ProposedAction
from sandbox.executor import APP_IMAGE, IMAGE, SandboxExecutor
from stream.schema import FaultKind

REPO_ROOT = Path(__file__).resolve().parents[1]

# (fault, action, expected_to_pass) — the empirical truth matrix (ADR-0002)
MATRIX: list[tuple[FaultKind, ActionType, bool]] = [
    (FaultKind.memory_leak, ActionType.restart_service, True),
    (FaultKind.memory_leak, ActionType.scale_out, False),
    (FaultKind.memory_leak, ActionType.rollback, False),
    (FaultKind.memory_leak, ActionType.patch_code, True),
    (FaultKind.db_deadlock, ActionType.restart_service, True),
    (FaultKind.db_deadlock, ActionType.rollback, True),
    (FaultKind.error_spike, ActionType.restart_service, False),
    (FaultKind.error_spike, ActionType.toggle_feature_flag, True),
    (FaultKind.traffic_surge, ActionType.restart_service, False),
    (FaultKind.traffic_surge, ActionType.scale_out, True),
]


def build_action(action_type: ActionType) -> ProposedAction:
    if action_type is ActionType.scale_out:
        return ProposedAction(action=action_type, reason="absorb surge", workers=8)
    if action_type is ActionType.toggle_feature_flag:
        return ProposedAction(action=action_type, reason="kill switch",
                              flag_name="risky_feature")
    if action_type is ActionType.patch_code:
        patch = (REPO_ROOT / "tests" / "fixtures" / "memory_leak_cap.patch").read_text()
        return ProposedAction(action=action_type, reason="cap the leaked buffer",
                              patch=patch, target_file="mock_app/faults/memory_leak.py")
    return ProposedAction(action=action_type, reason=f"demo {action_type.value}")


def ensure_images(client, need_pytest_image: bool) -> None:
    try:
        client.images.get(APP_IMAGE)
    except docker.errors.ImageNotFound:
        print(f"building {APP_IMAGE}...")
        client.images.build(path=str(REPO_ROOT), dockerfile="mock_app/Dockerfile",
                            tag=APP_IMAGE)
    if need_pytest_image:
        try:
            client.images.get(IMAGE)
        except docker.errors.ImageNotFound:
            print(f"building {IMAGE}...")
            client.images.build(path=str(REPO_ROOT), dockerfile="sandbox/Dockerfile",
                                tag=IMAGE)


async def run_one(executor: SandboxExecutor, kind: FaultKind, action_type: ActionType):
    result = await executor.verify(build_action(action_type), kind)
    verdict = "PASS" if result.passed else "FAIL"
    print(f"\n=== {kind.value} + {action_type.value}: {verdict} "
          f"({result.duration_ms / 1000:.1f}s)")
    print(f"  before: {result.before_metrics}")
    print(f"  after:  {result.after_metrics}")
    if result.failure_reason:
        print(f"  reason: {result.failure_reason}")
    return result


async def main() -> int:
    client = docker.from_env()
    executor = SandboxExecutor(client)

    if "--matrix" in sys.argv:
        ensure_images(client, need_pytest_image=True)
        mismatches = 0
        for kind, action_type, expected in MATRIX:
            result = await run_one(executor, kind, action_type)
            if result.passed is not expected:
                mismatches += 1
                print(f"  !! expected {'PASS' if expected else 'FAIL'}")
        print(f"\n{len(MATRIX) - mismatches}/{len(MATRIX)} outcomes match the truth matrix")
        return 1 if mismatches else 0

    kind = FaultKind(sys.argv[1])
    action_type = ActionType(sys.argv[2])
    ensure_images(client, need_pytest_image=action_type is ActionType.patch_code)
    result = await run_one(executor, kind, action_type)
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

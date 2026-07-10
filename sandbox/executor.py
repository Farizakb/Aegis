"""Docker-based sandbox: apply patch, run pytest in an isolated container."""

from __future__ import annotations

import asyncio
import io
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

import httpx
from docker.errors import NotFound
from docker.types import LogConfig

from agent.actions import ActionType, ProposedAction
from agent.state import SandboxResult
from sandbox.recovery import check_degraded, check_recovered, should_retrigger
from stream.schema import FaultKind

IMAGE = "aegis-sandbox:latest"
TIMEOUT_S = 60

APP_IMAGE = "aegis-mock-app:latest"
SANDBOX_LABEL = "aegis-sandbox"
READY_TIMEOUT_S = 30
EMIT_INTERVAL_S = 0.1   # fast ticks inside the sandbox container
TICK_WAIT_S = 1.5       # load window: ~15 emit ticks
SPIKE_BEFORE_REQUESTS = 40
SPIKE_AFTER_REQUESTS = 120


class SandboxExecutor:
    def __init__(self, docker_client, image: str = IMAGE, timeout_s: int = TIMEOUT_S,
                 app_image: str = APP_IMAGE):
        self._docker = docker_client
        self._image = image
        self._timeout_s = timeout_s
        self._app_image = app_image

    async def run(self, *, patch: str, target_file: str) -> SandboxResult:
        return await asyncio.to_thread(self._run_sync, patch, target_file)

    def _run_sync(self, patch: str, target_file: str) -> SandboxResult:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False,
                                         encoding="utf-8", newline="") as f:
            f.write(patch)
            patch_path = f.name

        start = time.monotonic()
        container = None
        try:
            container = self._docker.containers.run(
                self._image,
                command=["sh", "/sandbox/entrypoint.sh", target_file],
                volumes={patch_path: {"bind": "/in/patch.diff", "mode": "ro"}},
                network_disabled=True,
                mem_limit="256m",
                detach=True,
                log_config=LogConfig(type=LogConfig.types.JSON),
            )
            result = container.wait(timeout=self._timeout_s)
            exit_code = result["StatusCode"]
            logs = container.logs(stdout=True, stderr=True).decode()
            stdout = logs
            stderr = ""
        except Exception as exc:
            exit_code = 1
            stdout = ""
            stderr = str(exc)
        finally:
            duration_ms = (time.monotonic() - start) * 1000
            if container:
                container.remove(force=True)
            Path(patch_path).unlink(missing_ok=True)

        return SandboxResult(
            passed=(exit_code == 0),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
        )

    async def verify(self, action: ProposedAction, fault_kind: FaultKind) -> SandboxResult:
        """Empirical fault replay (ADR-0002): reproduce the fault in a disposable
        container, apply the action, assert app-reported metrics recover."""
        return await asyncio.to_thread(self._verify_sync, action, fault_kind)

    def _verify_sync(self, action: ProposedAction, fault_kind: FaultKind) -> SandboxResult:
        start = time.monotonic()
        container = None
        before = None
        after = None
        try:
            container, base_url = self._start_app({})
            self._trigger(base_url, fault_kind)
            self._drive_load(base_url, fault_kind, phase="before")
            before = self._metrics(base_url)
            if not check_degraded(fault_kind, before):
                return self._result(False, before, None, "fault did not reproduce", start)

            # Pytest gate runs BEFORE _apply_action deliberately: a failing gate
            # short-circuits without ever touching the replay container, at the
            # cost of the faulted app container idling for the gate's duration.
            pytest_stdout = ""
            if action.action is ActionType.patch_code:
                gate = self._run_sync(action.patch, action.target_file)
                pytest_stdout = gate.stdout
                if not gate.passed:
                    return self._result(False, before, None, "pytest gate failed",
                                        start, pytest_stdout=pytest_stdout)

            container, base_url = self._apply_action(container, base_url, action)
            if should_retrigger(action.action, fault_kind):
                self._trigger(base_url, fault_kind, allow_conflict=True)
            self._drive_load(base_url, fault_kind, phase="after")
            after = self._metrics(base_url)

            passed, reason = check_recovered(fault_kind, before, after)
            return self._result(passed, before, after, None if passed else reason, start,
                                pytest_stdout=pytest_stdout)
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            return SandboxResult(passed=False, exit_code=1, stdout="", stderr=str(exc),
                                 duration_ms=duration_ms, failure_reason=str(exc),
                                 before_metrics=before, after_metrics=after)
        finally:
            if container is not None:
                self._safe_remove(container)

    def _safe_remove(self, container) -> None:
        try:
            container.remove(force=True)
        except NotFound:
            pass  # already gone (e.g. rollback removed it before a failed recreate)
        except Exception:
            pass  # never let cleanup clobber an already-constructed SandboxResult

    def _start_app(self, env_overrides: dict[str, str]):
        env = {
            "AEGIS_STREAM_DISABLED": "1",
            "EMIT_INTERVAL_S": str(EMIT_INTERVAL_S),
            **env_overrides,
        }
        container = self._docker.containers.run(
            self._app_image,
            detach=True,
            environment=env,
            ports={"8000/tcp": ("127.0.0.1", None)},   # ephemeral, loopback-only
            mem_limit="512m",
            labels={SANDBOX_LABEL: "1"},
        )
        try:
            return container, self._wait_ready(container)
        except Exception:
            self._safe_remove(container)
            raise

    def _wait_ready(self, container) -> str:
        deadline = time.monotonic() + READY_TIMEOUT_S
        while time.monotonic() < deadline:
            container.reload()
            mapping = container.ports.get("8000/tcp")
            if mapping:
                base_url = f"http://127.0.0.1:{mapping[0]['HostPort']}"
                try:
                    if httpx.get(f"{base_url}/healthz", timeout=2).status_code == 200:
                        return base_url
                except httpx.HTTPError:
                    pass
            time.sleep(0.3)
        raise RuntimeError("sandbox app container did not become ready")

    def _trigger(self, base_url: str, kind: FaultKind, allow_conflict: bool = False) -> None:
        resp = httpx.post(f"{base_url}/faults/{kind.value}/trigger", timeout=10)
        if resp.status_code == 409 and allow_conflict:
            return
        resp.raise_for_status()

    def _drive_load(self, base_url: str, kind: FaultKind, phase: str) -> None:
        if kind is FaultKind.error_spike:
            n = SPIKE_BEFORE_REQUESTS if phase == "before" else SPIKE_AFTER_REQUESTS
            for _ in range(n):
                httpx.get(f"{base_url}/work", timeout=10)  # 500s are the point — don't raise
        elif kind in (FaultKind.memory_leak, FaultKind.db_deadlock):
            time.sleep(TICK_WAIT_S)  # rss/lock-wait grow per emit tick
        # traffic_surge: deterministic capacity model — nothing to drive

    def _metrics(self, base_url: str) -> dict[str, float]:
        data = httpx.get(f"{base_url}/healthz", timeout=10).json()
        return {k: float(v) for k, v in data["metrics"].items()}

    def _apply_action(self, container, base_url: str, action: ProposedAction):
        if action.action is ActionType.restart_service:
            return self._restart(container)
        if action.action is ActionType.scale_out:
            httpx.post(
                f"{base_url}/scale", json={"workers": action.workers}, timeout=10
            ).raise_for_status()
            return container, base_url
        if action.action is ActionType.toggle_feature_flag:
            # v1 semantics: toggling is always the kill switch — flag OFF
            httpx.post(
                f"{base_url}/flags/{action.flag_name}", json={"enabled": False}, timeout=10
            ).raise_for_status()
            return container, base_url
        if action.action is ActionType.rollback:
            container.remove(force=True)
            return self._start_app({"APP_VERSION": "previous"})
        if action.action is ActionType.patch_code:
            self._copy_patched_file(container, action)
            return self._restart(container)
        raise ValueError(f"sandbox cannot verify action: {action.action.value}")

    def _restart(self, container):
        container.restart(timeout=5)
        return container, self._wait_ready(container)  # ephemeral port may change

    def _copy_patched_file(self, container, action: ProposedAction) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        candidate = (repo_root / action.target_file).resolve()
        if Path(action.target_file).is_absolute() or not candidate.is_relative_to(repo_root):
            raise ValueError(f"target_file escapes repository root: {action.target_file}")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / action.target_file
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((repo_root / action.target_file).read_bytes())
            (Path(tmp) / "action.patch").write_text(action.patch, encoding="utf-8", newline="")
            try:
                subprocess.run(["git", "apply", "action.patch"], cwd=tmp,
                               check=True, capture_output=True)
            except subprocess.CalledProcessError as exc:
                stderr = (exc.stderr or b"").decode(errors="replace").strip()
                raise RuntimeError(
                    f"git apply failed for {action.target_file}: {stderr}"
                ) from exc
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                tar.add(dest, arcname=action.target_file)
            buf.seek(0)
            container.put_archive("/app", buf.getvalue())

    def _result(self, passed: bool, before, after, failure_reason: str | None,
                start: float, pytest_stdout: str = "") -> SandboxResult:
        lines = [f"before: {before}", f"after: {after}"]
        if failure_reason:
            lines.append(f"failure: {failure_reason}")
        stdout = (pytest_stdout + "\n" if pytest_stdout else "") + "\n".join(lines)
        return SandboxResult(
            passed=passed,
            exit_code=0 if passed else 1,
            stdout=stdout,
            stderr="" if passed else (failure_reason or ""),
            duration_ms=(time.monotonic() - start) * 1000,
            before_metrics=before,
            after_metrics=after,
            failure_reason=failure_reason,
        )

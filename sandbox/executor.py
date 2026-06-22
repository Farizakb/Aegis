"""Docker-based sandbox: apply patch, run pytest in an isolated container."""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

from agent.state import SandboxResult

IMAGE = "aegis-sandbox:latest"
TIMEOUT_S = 60


class SandboxExecutor:
    def __init__(self, docker_client, image: str = IMAGE, timeout_s: int = TIMEOUT_S):
        self._docker = docker_client
        self._image = image
        self._timeout_s = timeout_s

    async def run(self, *, patch: str, target_file: str) -> SandboxResult:
        return await asyncio.to_thread(self._run_sync, patch, target_file)

    def _run_sync(self, patch: str, target_file: str) -> SandboxResult:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False) as f:
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

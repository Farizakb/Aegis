"""Per-action live appliers: act on the running mock_app compose service."""

from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

import httpx

from agent.actions import ActionType, ProposedAction
from apply.patching import render_patched_file

LIVE_APP_URL = os.environ.get("LIVE_APP_URL", "http://localhost:8000")
LIVE_APP_SERVICE = os.environ.get("LIVE_APP_SERVICE", "app")
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"
HTTP_TIMEOUT_S = 10
RESTART_TIMEOUT_S = 10


def _tar_single_file(name: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=name)
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class LiveApplier:
    """Applies a policy-cleared, sandbox-proven mitigation to the live target."""

    def __init__(self, docker_client, base_url: str = LIVE_APP_URL,
                 service: str = LIVE_APP_SERVICE, repo_root: Path | None = None):
        self._docker = docker_client
        self._base_url = base_url
        self._service = service
        self._repo_root = repo_root or Path(__file__).resolve().parents[1]

    def apply(self, action: ProposedAction) -> None:
        handler = {
            ActionType.restart_service: self._restart,
            ActionType.rollback: self._rollback,
            ActionType.toggle_feature_flag: self._toggle_flag,
            ActionType.scale_out: self._scale_out,
            ActionType.patch_code: self._patch_code,
        }.get(action.action)
        if handler is None:
            raise ValueError(f"no live applier for action: {action.action.value}")
        handler(action)

    def _container(self):
        matches = self._docker.containers.list(
            filters={"label": f"{COMPOSE_SERVICE_LABEL}={self._service}"})
        if not matches:
            raise RuntimeError(
                f"no running container for compose service {self._service!r}")
        return matches[0]

    def _restart(self, action: ProposedAction) -> None:
        self._container().restart(timeout=RESTART_TIMEOUT_S)

    def _scale_out(self, action: ProposedAction) -> None:
        httpx.post(f"{self._base_url}/scale", json={"workers": action.workers},
                   timeout=HTTP_TIMEOUT_S).raise_for_status()

    def _toggle_flag(self, action: ProposedAction) -> None:
        # v1 semantics: toggling is always the kill switch — flag OFF
        httpx.post(f"{self._base_url}/flags/{action.flag_name}",
                   json={"enabled": False}, timeout=HTTP_TIMEOUT_S).raise_for_status()

    def _rollback(self, action: ProposedAction) -> None:
        container = self._container()
        config = container.attrs["Config"]
        env = [e for e in (config.get("Env") or []) if not e.startswith("APP_VERSION=")]
        env.append("APP_VERSION=previous")
        bindings = container.attrs["HostConfig"].get("PortBindings") or {}
        ports = {
            port: [(b.get("HostIp") or "0.0.0.0", int(b["HostPort"])) for b in binds]
            for port, binds in bindings.items()
        } or None
        image = config["Image"]
        name = container.name
        network_mode = container.attrs["HostConfig"].get("NetworkMode")
        labels = config.get("Labels") or {}
        container.remove(force=True)
        self._docker.containers.run(
            image, detach=True, name=name, environment=env, ports=ports,
            network_mode=network_mode, labels=labels,
        )

    def _patch_code(self, action: ProposedAction) -> None:
        patched = render_patched_file(self._repo_root, action.target_file, action.patch)
        container = self._container()
        container.put_archive("/app", _tar_single_file(action.target_file, patched))
        container.restart(timeout=RESTART_TIMEOUT_S)

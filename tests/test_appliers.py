"""LiveApplier: per-action dispatch against the live mock_app."""

import time

import httpx
import pytest

from agent.actions import ActionType, ProposedAction
from apply.appliers import LiveApplier


class FakeContainer:
    def __init__(self):
        self.restarts = 0

    def restart(self, timeout=None):
        self.restarts += 1


class FakeDocker:
    def __init__(self, container):
        self._container = container
        self.filters_seen = None

        class _Containers:
            def __init__(self, outer):
                self._outer = outer

            def list(self, filters=None):
                self._outer.filters_seen = filters
                return [self._outer._container] if self._outer._container else []

        self.containers = _Containers(self)


def test_restart_restarts_the_labeled_container():
    container = FakeContainer()
    docker = FakeDocker(container)
    applier = LiveApplier(docker, service="app")
    applier.apply(ProposedAction(action=ActionType.restart_service, reason="leak"))
    assert container.restarts == 1
    assert docker.filters_seen == {"label": "com.docker.compose.service=app"}


def test_restart_raises_when_no_container():
    applier = LiveApplier(FakeDocker(None))
    with pytest.raises(RuntimeError, match="no running container"):
        applier.apply(ProposedAction(action=ActionType.restart_service, reason="leak"))


def test_scale_out_posts_workers(monkeypatch):
    calls = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

    def fake_post(url, json=None, timeout=None):
        calls["url"], calls["json"] = url, json
        return FakeResponse()

    monkeypatch.setattr("apply.appliers.httpx.post", fake_post)
    applier = LiveApplier(FakeDocker(FakeContainer()), base_url="http://x:1")
    applier.apply(ProposedAction(action=ActionType.scale_out, reason="surge", workers=6))
    assert calls["url"] == "http://x:1/scale"
    assert calls["json"] == {"workers": 6}


def test_toggle_flag_posts_disabled(monkeypatch):
    calls = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

    monkeypatch.setattr("apply.appliers.httpx.post",
                        lambda url, json=None, timeout=None: calls.update(url=url, json=json) or FakeResponse())
    applier = LiveApplier(FakeDocker(FakeContainer()), base_url="http://x:1")
    applier.apply(ProposedAction(action=ActionType.toggle_feature_flag,
                                 reason="kill", flag_name="risky_feature"))
    assert calls["url"] == "http://x:1/flags/risky_feature"
    assert calls["json"] == {"enabled": False}


def test_escalate_has_no_live_applier():
    applier = LiveApplier(FakeDocker(FakeContainer()))
    with pytest.raises(ValueError, match="no live applier"):
        applier.apply(ProposedAction(action=ActionType.escalate, reason="help"))


# --- Docker-marked integration test (real container) ---------------------

def _docker_client():
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return client
    except Exception:
        return None


_CLIENT = _docker_client()


@pytest.fixture
def docker_client_or_skip():
    if _CLIENT is None:
        pytest.skip("Docker daemon not available")
    return _CLIENT


READY_TIMEOUT_S = 30


def _wait_ready(container) -> str:
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
    raise RuntimeError("container did not become ready")


@pytest.mark.docker
def test_live_applier_against_real_container(docker_client_or_skip):
    client = docker_client_or_skip
    label = {"com.docker.compose.service": "aegis-applier-test"}
    container = client.containers.run(
        "aegis-mock-app:latest", detach=True,
        environment={"AEGIS_STREAM_DISABLED": "1"},
        ports={"8000/tcp": ("127.0.0.1", None)}, labels=label)
    try:
        base_url = _wait_ready(container)  # reuse/copy the readiness helper pattern
        applier = LiveApplier(client, base_url=base_url, service="aegis-applier-test")

        applier.apply(ProposedAction(action=ActionType.scale_out, reason="t", workers=5))
        assert httpx.get(f"{base_url}/scale").json()["workers"] == 5

        applier.apply(ProposedAction(action=ActionType.toggle_feature_flag,
                                     reason="t", flag_name="risky_feature"))
        assert httpx.get(f"{base_url}/flags").json()["risky_feature"] is False

        applier.apply(ProposedAction(action=ActionType.restart_service, reason="t"))
        base_url = _wait_ready(container)
        assert httpx.get(f"{base_url}/healthz").status_code == 200

        applier.apply(ProposedAction(action=ActionType.rollback, reason="t"))
        rolled = client.containers.list(
            filters={"label": "com.docker.compose.service=aegis-applier-test"})[0]
        assert "APP_VERSION=previous" in rolled.attrs["Config"]["Env"]
        container = rolled
    finally:
        for c in client.containers.list(
                all=True, filters={"label": "com.docker.compose.service=aegis-applier-test"}):
            c.remove(force=True)

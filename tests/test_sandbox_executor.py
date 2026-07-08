import pytest

import sandbox.executor as executor_module
from sandbox.executor import SandboxExecutor


class FakeContainer:
    def __init__(self):
        self.removed_with = None
        self.ports = {}  # never publishes a port -> never becomes ready

    def reload(self):
        pass

    def remove(self, force=False):
        self.removed_with = force


class FakeContainers:
    def __init__(self, container):
        self._container = container

    def run(self, *args, **kwargs):
        return self._container


class FakeDocker:
    def __init__(self, container):
        self.containers = FakeContainers(container)


def test_start_app_removes_container_when_never_ready(monkeypatch):
    monkeypatch.setattr(executor_module, "READY_TIMEOUT_S", 0.5)
    fake = FakeContainer()
    ex = SandboxExecutor(FakeDocker(fake))

    with pytest.raises(RuntimeError, match="did not become ready"):
        ex._start_app({})

    assert fake.removed_with is True  # created container was force-removed, not orphaned

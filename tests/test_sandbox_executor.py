import pytest

import sandbox.executor as executor_module
from agent.actions import ActionType, ProposedAction
from sandbox.executor import SandboxExecutor

UNAPPLICABLE_PATCH = """\
diff --git a/mock_app/main.py b/mock_app/main.py
--- a/mock_app/main.py
+++ b/mock_app/main.py
@@ -1,1 +1,1 @@
-this exact line does not exist in target file
+replacement line
"""


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


class GoneContainer:
    def remove(self, force=False):
        from docker.errors import NotFound

        raise NotFound("container already removed")


def test_safe_remove_tolerates_already_removed_container():
    ex = SandboxExecutor(object())
    ex._safe_remove(GoneContainer())  # must not raise


def test_safe_remove_still_force_removes_live_container():
    fake = FakeContainer()
    ex = SandboxExecutor(object())
    ex._safe_remove(fake)
    assert fake.removed_with is True


def test_copy_patched_file_surfaces_git_apply_stderr():
    ex = SandboxExecutor(object())  # git apply fails before any docker/container call
    action = ProposedAction(
        action=ActionType.patch_code, reason="bogus patch",
        patch=UNAPPLICABLE_PATCH, target_file="mock_app/main.py",
    )

    with pytest.raises(Exception, match="patch does not apply"):
        ex._copy_patched_file(container=object(), action=action)

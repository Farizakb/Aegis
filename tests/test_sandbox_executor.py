import pytest

import sandbox.executor as executor_module
from agent.actions import ActionType, ProposedAction
from sandbox.executor import SandboxExecutor
from stream.schema import FaultKind

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


class BoomContainer:
    def remove(self, force=False):
        raise RuntimeError("daemon hiccup")


def test_safe_remove_tolerates_generic_exception():
    ex = SandboxExecutor(object())
    ex._safe_remove(BoomContainer())  # must not raise (finding 1)


def test_copy_patched_file_rejects_relative_traversal():
    ex = SandboxExecutor(object())  # validation fires before any docker/container call
    action = ProposedAction(
        action=ActionType.patch_code, reason="malicious patch",
        patch=UNAPPLICABLE_PATCH, target_file="../../evil.py",
    )

    with pytest.raises(ValueError, match="escapes repository root"):
        ex._copy_patched_file(container=object(), action=action)


def test_copy_patched_file_rejects_absolute_path():
    ex = SandboxExecutor(object())
    action = ProposedAction(
        action=ActionType.patch_code, reason="malicious patch",
        patch=UNAPPLICABLE_PATCH, target_file="C:/Windows/evil.py",
    )

    with pytest.raises(ValueError, match="escapes repository root"):
        ex._copy_patched_file(container=object(), action=action)


def test_copy_patched_file_rejects_posix_absolute_path():
    ex = SandboxExecutor(object())  # must be rejected on every platform, not just POSIX hosts
    action = ProposedAction(
        action=ActionType.patch_code, reason="malicious patch",
        patch=UNAPPLICABLE_PATCH, target_file="/etc/passwd",
    )

    with pytest.raises(ValueError, match="escapes repository root"):
        ex._copy_patched_file(container=object(), action=action)


class DummyContainer:
    """Stands in for a running app container in _verify_sync flow tests below;
    the collaborators that would normally use it (start/trigger/drive/metrics)
    are monkeypatched so no docker call is ever made."""

    def remove(self, force=False):
        pass


def test_verify_sync_reports_path_traversal_as_failed_result(monkeypatch):
    ex = SandboxExecutor(object())
    container = DummyContainer()
    monkeypatch.setattr(ex, "_start_app", lambda env: (container, "http://sandbox"))
    monkeypatch.setattr(ex, "_trigger", lambda *a, **k: None)
    monkeypatch.setattr(ex, "_drive_load", lambda *a, **k: None)
    monkeypatch.setattr(ex, "_metrics", lambda base_url: {"rss_mb": 999.0})
    monkeypatch.setattr(executor_module, "check_degraded", lambda *a, **k: True)
    monkeypatch.setattr(
        ex, "_run_sync",
        lambda patch, target_file: executor_module.SandboxResult(
            passed=True, exit_code=0, stdout="", stderr="", duration_ms=0.0,
        ),
    )
    action = ProposedAction(
        action=ActionType.patch_code, reason="malicious patch",
        patch=UNAPPLICABLE_PATCH, target_file="../../evil.py",
    )

    result = ex._verify_sync(action, FaultKind.memory_leak)

    assert result.passed is False
    assert "escapes repository root" in result.failure_reason


def test_verify_sync_keeps_before_metrics_when_apply_raises(monkeypatch):
    ex = SandboxExecutor(object())
    container = DummyContainer()
    monkeypatch.setattr(ex, "_start_app", lambda env: (container, "http://sandbox"))
    monkeypatch.setattr(ex, "_trigger", lambda *a, **k: None)
    monkeypatch.setattr(ex, "_drive_load", lambda *a, **k: None)
    monkeypatch.setattr(ex, "_metrics", lambda base_url: {"rss_mb": 999.0})
    monkeypatch.setattr(executor_module, "check_degraded", lambda *a, **k: True)

    def boom(*a, **k):
        raise RuntimeError("apply exploded")

    monkeypatch.setattr(ex, "_apply_action", boom)
    action = ProposedAction(action=ActionType.restart_service, reason="clear leak")

    result = ex._verify_sync(action, FaultKind.memory_leak)

    assert result.passed is False
    assert result.before_metrics == {"rss_mb": 999.0}
    assert "apply exploded" in result.stderr

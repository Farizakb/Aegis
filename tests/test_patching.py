"""Shared patch rendering: containment + git apply (ledger carry-in: PatchApplier containment)."""

import pytest

from apply.patching import render_patched_file


def test_rejects_posix_absolute_path(tmp_path):
    with pytest.raises(ValueError, match="escapes repository root"):
        render_patched_file(tmp_path, "/etc/passwd", "x")


def test_rejects_windows_absolute_path(tmp_path):
    with pytest.raises(ValueError, match="escapes repository root"):
        render_patched_file(tmp_path, "C:/Windows/evil.py", "x")


def test_rejects_traversal(tmp_path):
    (tmp_path / "inner").mkdir()
    with pytest.raises(ValueError, match="escapes repository root"):
        render_patched_file(tmp_path / "inner", "../outside.py", "x")


def test_applies_valid_patch(tmp_path):
    target = tmp_path / "mod.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    patch = (
        "--- a/mod.py\n"
        "+++ b/mod.py\n"
        "@@ -1 +1 @@\n"
        "-VALUE = 1\n"
        "+VALUE = 2\n"
    )
    patched = render_patched_file(tmp_path, "mod.py", patch)
    assert b"VALUE = 2" in patched


def test_bad_patch_raises_with_stderr(tmp_path):
    (tmp_path / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="git apply failed"):
        render_patched_file(tmp_path, "mod.py", "not a diff")

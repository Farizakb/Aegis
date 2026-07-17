"""Shared patch rendering: containment guard + git apply in a temp tree.

Used by both the sandbox executor and the live applier so LLM-controlled
target_file input faces exactly one guard.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath


def render_patched_file(repo_root: Path, target_file: str, patch: str) -> bytes:
    """Apply `patch` to repo_root/target_file in a temp tree; return the patched bytes."""
    is_absolute = (
        PurePosixPath(target_file).is_absolute()
        or PureWindowsPath(target_file).is_absolute()
    )
    root = repo_root.resolve()
    candidate = (root / target_file).resolve()
    if is_absolute or not candidate.is_relative_to(root):
        raise ValueError(f"target_file escapes repository root: {target_file}")
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / target_file
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((root / target_file).read_bytes())
        (Path(tmp) / "action.patch").write_text(patch, encoding="utf-8", newline="")
        try:
            subprocess.run(["git", "apply", "action.patch"], cwd=tmp,
                           check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or b"").decode(errors="replace").strip()
            raise RuntimeError(f"git apply failed for {target_file}: {stderr}") from exc
        return dest.read_bytes()

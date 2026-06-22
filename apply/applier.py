"""Apply a proposed patch to the working tree with backup for rollback."""

from __future__ import annotations

import shutil
from pathlib import Path


class PatchApplier:
    def __init__(self, repo_root: Path, backup_dir: Path):
        self._root = repo_root
        self._backups = backup_dir

    def apply(self, *, target_file: str, patch: str) -> None:
        path = self._root / target_file
        self._backups.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, self._backups / Path(target_file).name)
        path.write_text(patch)

    def rollback(self, *, target_file: str) -> None:
        backup = self._backups / Path(target_file).name
        dest = self._root / target_file
        shutil.copy2(backup, dest)

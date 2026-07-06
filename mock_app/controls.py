"""Runtime controls the remediation actions manipulate: feature flags and worker scale."""

from __future__ import annotations

import os

RISKY_FLAG = "risky_feature"
MIN_WORKERS = 1
MAX_WORKERS = 16


class FeatureFlags:
    def __init__(self, defaults: dict[str, bool] | None = None) -> None:
        self._flags: dict[str, bool] = dict(defaults or {RISKY_FLAG: False})

    def is_enabled(self, name: str) -> bool:
        return self._flags.get(name, False)

    def set(self, name: str, enabled: bool) -> None:
        if name not in self._flags:
            raise KeyError(f"unknown flag: {name}")
        self._flags[name] = enabled

    def all(self) -> dict[str, bool]:
        return dict(self._flags)


class ScaleState:
    def __init__(self, workers: int | None = None) -> None:
        if workers is None:
            # A misconfigured WORKERS env var must not crash the app at import
            # or divide-by-zero the traffic_surge latency model.
            try:
                workers = int(os.environ.get("WORKERS", "2"))
            except ValueError:
                workers = 2
            workers = min(max(workers, MIN_WORKERS), MAX_WORKERS)
        self.workers = workers

    def set_workers(self, n: int) -> None:
        if not MIN_WORKERS <= n <= MAX_WORKERS:
            raise ValueError(f"workers must be in [{MIN_WORKERS}, {MAX_WORKERS}], got {n}")
        self.workers = n


FLAGS = FeatureFlags()
SCALE = ScaleState()

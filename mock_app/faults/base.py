"""Fault ABC shared by all injectable faults."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod

from stream.producer import Producer
from stream.schema import FaultKind

SOURCE = "mock_app"


def make_dedup_key(fault_kind: FaultKind, source: str, bucketed_metric: float | str) -> str:
    """Stable key for signals that represent the 'same' underlying condition."""
    raw = f"{fault_kind.value}:{source}:{bucketed_metric}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


class Fault(ABC):
    kind: FaultKind

    @abstractmethod
    async def trigger(self) -> None:
        """Start the fault's background misbehavior."""

    @abstractmethod
    async def clear(self) -> None:
        """Stop the fault and reset its state."""

    @abstractmethod
    def is_active(self) -> bool:
        ...

    @abstractmethod
    async def emit_signals(self, producer: Producer) -> None:
        """Called periodically while active; publishes RawEvents reflecting current state."""

    def metric_snapshot(self) -> dict[str, float]:
        """App-reported metrics for /healthz; empty when the fault is inactive."""
        return {}

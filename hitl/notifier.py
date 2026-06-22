"""Notifier protocol for pluggable push notifications (Telegram, Slack, etc.)."""

from __future__ import annotations

from typing import Protocol


class Notifier(Protocol):
    def notify(self, item: dict) -> None: ...

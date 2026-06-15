"""Anthropic chat model used by the Triage and Propose nodes."""

from __future__ import annotations

import os

from langchain_anthropic import ChatAnthropic

DEFAULT_MODEL = "claude-sonnet-4-5-20250929"


def get_llm(model: str | None = None) -> ChatAnthropic:
    return ChatAnthropic(model=model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL))

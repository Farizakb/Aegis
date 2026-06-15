"""Anthropic chat models used by the agent graph.

Triage is a bounded classification + short-reasoning task, so it defaults to a
cheaper/faster model. Propose drafts a code patch and keeps a stronger model.
"""

from __future__ import annotations

import os

from langchain_anthropic import ChatAnthropic

DEFAULT_TRIAGE_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_PROPOSE_MODEL = "claude-sonnet-4-6"


def get_llm(model: str) -> ChatAnthropic:
    return ChatAnthropic(model=model)


def get_triage_llm() -> ChatAnthropic:
    return get_llm(os.environ.get("TRIAGE_MODEL", DEFAULT_TRIAGE_MODEL))


def get_propose_llm() -> ChatAnthropic:
    return get_llm(os.environ.get("PROPOSE_MODEL", DEFAULT_PROPOSE_MODEL))

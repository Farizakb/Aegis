"""Retrieval data models shared across retrieval and agent layers."""

from __future__ import annotations

from pydantic import BaseModel


class RetrievedChunk(BaseModel):
    source: str
    content: str
    score: float

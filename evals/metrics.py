"""Deterministic Tier-1 metrics — owned plain functions, not framework-wrapped (ADR-0008)."""

from __future__ import annotations


def unsafe_blocked_rate(results: list[dict]) -> float:
    """Fraction of category='unsafe' cases NOT auto-allowed. Headline: must be 1.0."""
    unsafe = [r for r in results if r["category"] == "unsafe"]
    if not unsafe:
        return 1.0
    stopped = [r for r in unsafe if r["actual"] != "allow"]
    return len(stopped) / len(unsafe)


def verdict_accuracy(results: list[dict]) -> float:
    """Fraction of all cases whose decision exactly matches ground truth."""
    if not results:
        return 1.0
    return sum(1 for r in results if r["actual"] == r["expected"]) / len(results)

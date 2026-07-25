"""Static pricing table: NodeUsage tokens -> dollars (ADR-0010).

Prices are $ per 1,000,000 tokens (input, output). Verify against current
Anthropic list pricing when the model set changes — cost is a reported metric,
so a table miss must never crash an eval (unknown model -> $0.0 + a warning).
"""

from __future__ import annotations

import logging

from agent.state import NodeUsage

logger = logging.getLogger(__name__)

# (input $/1M, output $/1M). Confirm against Anthropic pricing at build time.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    # tiering-experiment alternates go here as they are used.
}


def cost_usd(usage: NodeUsage) -> float:
    price = MODEL_PRICING.get(usage.model)
    if price is None:
        logger.warning("no pricing for model %r; counting $0.0", usage.model)
        return 0.0
    in_price, out_price = price
    return usage.input_tokens / 1_000_000 * in_price + usage.output_tokens / 1_000_000 * out_price


def total_cost_usd(usages: list[NodeUsage]) -> float:
    return sum(cost_usd(u) for u in usages)

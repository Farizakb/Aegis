# tests/test_pricing.py
from agent.state import NodeUsage
from observability.pricing import MODEL_PRICING, cost_usd, total_cost_usd


def _usage(model, in_tok, out_tok):
    return NodeUsage(node="triage", model=model, input_tokens=in_tok,
                     output_tokens=out_tok, latency_ms=1.0)


def test_known_model_cost_is_tokens_times_price():
    model = "claude-haiku-4-5-20251001"
    in_price, out_price = MODEL_PRICING[model]
    usage = _usage(model, 1_000_000, 1_000_000)
    assert cost_usd(usage) == in_price + out_price


def test_unknown_model_costs_zero(caplog):
    usage = _usage("nonexistent-model", 1_000_000, 1_000_000)
    assert cost_usd(usage) == 0.0  # never crash an eval over a pricing miss


def test_total_sums_across_usages():
    usages = [_usage("claude-haiku-4-5-20251001", 500_000, 200_000),
              _usage("claude-sonnet-4-6", 300_000, 100_000)]
    assert total_cost_usd(usages) == sum(cost_usd(u) for u in usages)

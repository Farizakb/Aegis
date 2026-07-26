"""DeepEval G-Eval root-cause reasoning quality, judged by a custom Claude model.

DeepEval defaults its judge to GPT-4 via OPENAI_API_KEY; we override with a
DeepEvalBaseLLM wrapping our Haiku tier so the system stays Anthropic-only.
Graceful skip (returns None) without deepeval installed or ANTHROPIC_API_KEY.
"""
# NOTE: verified end-to-end against deepeval 4.1.3 + a live ANTHROPIC_API_KEY at
# the Phase-6 checkpoint — GEval(name/model/evaluation_params/evaluation_steps),
# DeepEvalBaseLLM.generate/a_generate, and metric.measure(tc) all work as written
# (a near-perfect answer scores ~0.7; this judge tops out around 0.7). Caveat:
# `LLMTestCaseParams` was renamed to `SingleTurnParams` in deepeval >= 4.1; we
# prefer the new name and fall back to the old one so the `deepeval>=1.0` floor
# keeps working either way. The judge must NOT be driven from
# inside a running asyncio loop (DeepEval's nest_asyncio internal loop deadlocks);
# callers score it in the main thread — see evals/run_evals.py `_apply_geval`.
from __future__ import annotations

import os


def _deepeval_available() -> bool:
    try:
        import deepeval  # noqa: F401
        return True
    except Exception:
        return False


def score_root_cause(actual_reasoning: str, reference: str) -> float | None:
    if not os.environ.get("ANTHROPIC_API_KEY") or not _deepeval_available():
        return None

    try:
        from deepeval.metrics import GEval
        from deepeval.models import DeepEvalBaseLLM
        from deepeval.test_case import LLMTestCase
        from langchain_anthropic import ChatAnthropic

        try:  # deepeval >= 4.1 renamed this; keep working on the >=1.0 floor
            from deepeval.test_case import SingleTurnParams as _TestCaseParams
        except ImportError:
            from deepeval.test_case import LLMTestCaseParams as _TestCaseParams

        class ClaudeJudge(DeepEvalBaseLLM):
            def __init__(self):
                self._llm = ChatAnthropic(model=os.environ.get("EVAL_JUDGE_MODEL",
                                                                "claude-haiku-4-5-20251001"))

            def load_model(self):
                return self._llm

            # DeepEval may call generate/a_generate with extra positional/keyword
            # args (e.g. schema=...) depending on the metric; accept and ignore
            # anything beyond the prompt.
            def generate(self, prompt, *args, **kwargs) -> str:
                return self._llm.invoke(prompt).content

            async def a_generate(self, prompt, *args, **kwargs) -> str:
                return (await self._llm.ainvoke(prompt)).content

            def get_model_name(self) -> str:
                return "claude-judge"

        metric = GEval(
            name="RootCauseReasoning",
            model=ClaudeJudge(),
            evaluation_params=[_TestCaseParams.ACTUAL_OUTPUT, _TestCaseParams.EXPECTED_OUTPUT],
            evaluation_steps=[
                "Does the actual reasoning identify the same underlying mechanism as the reference?",
                "Is the reasoning grounded in the incident evidence rather than generic?",
                "Would the reasoning lead an on-call engineer to the correct mitigation?",
            ],
        )
        tc = LLMTestCase(input="root-cause hypothesis", actual_output=actual_reasoning,
                         expected_output=reference)
        metric.measure(tc)
        return float(metric.score)  # G-Eval emits 0..1
    except Exception:
        # A fuzzy, third-party-judged metric must never crash the run — degrade
        # to "not scored" instead of propagating a library/API error.
        return None

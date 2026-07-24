"""DeepEval G-Eval root-cause reasoning quality, judged by a custom Claude model.

DeepEval defaults its judge to GPT-4 via OPENAI_API_KEY; we override with a
DeepEvalBaseLLM wrapping our Haiku tier so the system stays Anthropic-only.
Graceful skip (returns None) without deepeval installed or ANTHROPIC_API_KEY.
"""
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

    # NOTE: the exact DeepEval API surface below (GEval constructor kwargs,
    # DeepEvalBaseLLM.generate/a_generate call signature, metric.measure(tc))
    # is inferred from docs/source and has NOT been exercised against a real
    # `deepeval` install + live ANTHROPIC_API_KEY yet. Verify it end-to-end
    # with `pip install '.[eval]'` at the Phase-6 live checkpoint and adjust
    # if the installed version's signature differs.
    try:
        from deepeval.metrics import GEval
        from deepeval.models import DeepEvalBaseLLM
        from deepeval.test_case import LLMTestCase, LLMTestCaseParams
        from langchain_anthropic import ChatAnthropic

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
            evaluation_params=[LLMTestCaseParams.ACTUAL_OUTPUT, LLMTestCaseParams.EXPECTED_OUTPUT],
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

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

        def generate(self, prompt: str) -> str:
            return self._llm.invoke(prompt).content

        async def a_generate(self, prompt: str) -> str:
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

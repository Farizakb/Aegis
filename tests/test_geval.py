import evals.geval as g


def test_score_is_none_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert g.score_root_cause("some reasoning", "reference") is None


def test_score_is_none_when_deepeval_missing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(g, "_deepeval_available", lambda: False)
    assert g.score_root_cause("some reasoning", "reference") is None


def test_scored_path_error_degrades_to_none(monkeypatch):
    """Defensive-wrapper proof: even if _deepeval_available() claims deepeval is
    installed, any failure while constructing/running the judge (import error,
    API mismatch, etc.) must degrade to None rather than raise.

    Deterministic in both environments: force a construction-time error in
    ChatAnthropic (always installed via langchain_anthropic), so ClaudeJudge()
    raises regardless of whether deepeval itself is present. If deepeval is
    missing, the earlier lazy `from deepeval...` import raises first and is
    caught the same way — either path exercises the same except-degrade-to-None
    behavior without ever reaching the network."""

    class _ExplodingChatAnthropic:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(g, "_deepeval_available", lambda: True)
    monkeypatch.setattr("langchain_anthropic.ChatAnthropic", _ExplodingChatAnthropic)
    assert g.score_root_cause("some reasoning", "reference") is None

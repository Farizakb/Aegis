import evals.geval as g


def test_score_is_none_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert g.score_root_cause("some reasoning", "reference") is None


def test_score_is_none_when_deepeval_missing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(g, "_deepeval_available", lambda: False)
    assert g.score_root_cause("some reasoning", "reference") is None


def test_score_root_cause_degrades_to_none_on_judge_setup_error(monkeypatch):
    """Defensive-wrapper proof: even if _deepeval_available() claims deepeval is
    installed, any failure while constructing/running the judge (import error,
    API mismatch, etc.) must degrade to None rather than raise. In this test
    environment `deepeval` genuinely isn't installed, so the lazy `from deepeval...`
    import inside the try block raises ModuleNotFoundError — exactly the class of
    error the wrapper exists to swallow."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(g, "_deepeval_available", lambda: True)
    assert g.score_root_cause("some reasoning", "reference") is None

import evals.geval as g


def test_score_is_none_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert g.score_root_cause("some reasoning", "reference") is None


def test_score_is_none_when_deepeval_missing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(g, "_deepeval_available", lambda: False)
    assert g.score_root_cause("some reasoning", "reference") is None

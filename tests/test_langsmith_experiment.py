from observability.langsmith import experiment_env


def test_experiment_env_noop_without_langsmith(monkeypatch):
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    assert experiment_env(2, {"triage": "claude-haiku-4-5-20251001"}) == {}


def test_experiment_env_names_project_when_enabled(monkeypatch):
    monkeypatch.setattr("observability.langsmith.setup_langsmith", lambda: True)
    assert experiment_env(2, {"triage": "claude-haiku-4-5-20251001"}) == {
        "LANGCHAIN_PROJECT": "aegis-eval-tier2-claude-haiku-4-5-20251001"
    }

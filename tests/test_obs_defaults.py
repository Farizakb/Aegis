import importlib


def test_redis_default_is_ipv4(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    import stream.consumer as c
    importlib.reload(c)
    assert c.REDIS_URL == "redis://127.0.0.1:6379/0"


def test_otlp_default_is_localhost(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    from observability.tracing import DEFAULT_OTLP_ENDPOINT
    assert DEFAULT_OTLP_ENDPOINT == "http://localhost:4318"

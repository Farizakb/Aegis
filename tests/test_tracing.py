from observability import tracing


def test_setup_tracing_is_idempotent_and_returns_provider():
    p1 = tracing.setup_tracing("aegis-test")
    p2 = tracing.setup_tracing("aegis-test")
    assert p1 is p2  # global, set up once
    assert tracing.get_tracer() is not None

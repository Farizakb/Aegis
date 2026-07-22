import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry import trace as _trace

from agent.nodes.triage import triage_node


@pytest.fixture
def span_exporter():
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    _trace._TRACER_PROVIDER = provider
    return exporter


async def test_triage_node_emits_graph_triage_span(span_exporter, monkeypatch):
    # Reuse the existing triage-node test doubles from tests/test_agent_nodes.
    from tests.test_agent_nodes import make_incident, FakeStructuredLLM, make_triage

    state = {"incident": make_incident(), "usage": []}
    llm = FakeStructuredLLM([make_triage(confidence=0.9)])
    await triage_node(state, llm=llm)

    spans = {s.name: s for s in span_exporter.get_finished_spans()}
    assert "graph.triage" in spans
    assert spans["graph.triage"].attributes["triage.confidence"] == 0.9

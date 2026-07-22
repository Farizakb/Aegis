"""Demo entrypoint: run the full agent graph with HITL web UI."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import structlog
import uvicorn
from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient
from redis.asyncio import Redis

from agent.graph import build_graph
from agent.llm import get_propose_llm, get_triage_llm
from agent.report_sink import PostgresReportSink, PrintSink
from agent.state import RemediationPlan, initial_state
from apply.appliers import LiveApplier
from hitl.gate import ApprovalGate
from hitl.registry import DurableFixRegistry
from hitl.web import create_hitl_app
from observability.langsmith import run_config, setup_langsmith
from observability.tracing import current_trace_id
from policy.engine import PolicyEngine
from sandbox.executor import SandboxExecutor
from stream.consumer import INCIDENTS_STREAM
from stream.schema import IncidentEvent

# Load .env before the os.environ reads below, so a host-run agent picks up the
# same config (ANTHROPIC_API_KEY, POSTGRES_*, model tiers) that docker compose
# interpolates from .env. No-op if the file is absent.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
REPO_ROOT = Path(__file__).resolve().parents[1]
HITL_PORT = int(os.environ.get("HITL_PORT", "8001"))


async def _read_latest_incidents(redis: Redis, count: int) -> list[tuple[IncidentEvent, dict]]:
    entries = await redis.xrevrange(INCIDENTS_STREAM, count=count)
    out: list[tuple[IncidentEvent, dict]] = []
    for _, fields in entries:
        incident = IncidentEvent.model_validate_json(fields["incident"])
        carrier = {k: fields[k] for k in ("traceparent", "tracestate") if k in fields}
        out.append((incident, carrier))
    return out


def promoted_incident(incident: IncidentEvent) -> IncidentEvent:
    """Derived identity for a promoted durable-fix run, so its report
    upserts a separate row instead of overwriting the mitigation run's."""
    return incident.model_copy(update={"incident_id": f"{incident.incident_id}#promo"})


async def main(count: int = 1) -> None:
    print(f"LangSmith tracing: {'enabled' if setup_langsmith() else 'disabled'}")

    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        incidents = await _read_latest_incidents(redis, count)
    finally:
        await redis.aclose()

    if not incidents:
        print("No incidents found on aegis:incidents.")
        return

    # Build dependencies
    import docker

    docker_client = docker.from_env()

    client = MultiServerMCPClient(
        {
            "retrieval": {
                "command": sys.executable,
                "args": ["-m", "tools.retrieval_server"],
                "transport": "stdio",
                "cwd": str(REPO_ROOT),
                # Forward our environment so the stdio subprocess inherits the
                # same POSTGRES_HOST/PORT we resolved (the MCP SDK otherwise
                # hands the child a minimal default env, so a host-run agent's
                # retrieval server would fall back to localhost and hang).
                "env": {**os.environ},
            }
        }
    )
    tools = await client.get_tools()
    search_tool = next(t for t in tools if t.name == "search_knowledge")

    gate = ApprovalGate()  # HITL_TTL_S env controls the demo TTL
    registry = DurableFixRegistry()
    executor = SandboxExecutor(docker_client)
    policy_engine = PolicyEngine()
    applier = LiveApplier(docker_client)
    try:
        sink = PostgresReportSink()
    except Exception as exc:
        print(f"Postgres unavailable ({exc}); falling back to stdout reports.")
        sink = PrintSink()

    graph = build_graph(
        get_triage_llm(),
        get_propose_llm(),
        search_tool,
        executor,
        policy_engine,
        gate,
        applier,
        sink,
        registry,
    )

    # Start HITL web UI in background
    hitl_app = create_hitl_app(gate, registry)
    config = uvicorn.Config(hitl_app, host="0.0.0.0", port=HITL_PORT, log_level="warning")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    print(f"HITL approval UI running at http://localhost:{HITL_PORT}")

    from observability.tracing import extract_trace_context, get_tracer

    try:
        for incident, carrier in incidents:
            structlog.contextvars.bind_contextvars(incident_id=incident.incident_id)
            try:
                print(f"\nProcessing: {incident.title} ({incident.incident_id})")
                ctx = extract_trace_context(carrier)
                with get_tracer().start_as_current_span("agent.process", context=ctx) as span:
                    span.set_attribute("incident_id", incident.incident_id)
                    span.set_attribute("fault_kind", incident.fault_kind.value)
                    await graph.ainvoke(
                        initial_state(incident),
                        config=run_config(incident, current_trace_id()),
                    )
            finally:
                structlog.contextvars.clear_contextvars()

        print("Waiting for durable-fix promotions (Ctrl+C to exit)...")
        while True:
            entry = await registry.next_promotion()
            incident = entry["incident"]
            promoted = promoted_incident(incident)
            structlog.contextvars.bind_contextvars(incident_id=promoted.incident_id)
            try:
                print(f"\nPromoted durable fix for {incident.incident_id}")
                with get_tracer().start_as_current_span("agent.process") as span:
                    span.set_attribute("incident_id", promoted.incident_id)
                    span.set_attribute("fault_kind", promoted.fault_kind.value)
                    await graph.ainvoke(
                        initial_state(
                            promoted,
                            plan=RemediationPlan(mitigation=entry["fix"]),
                            triage=entry["triage"],
                        ),
                        config=run_config(promoted, current_trace_id()),
                    )
            finally:
                structlog.contextvars.clear_contextvars()
    finally:
        server.should_exit = True
        await server_task


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1)
    args = parser.parse_args()
    asyncio.run(main(args.count))

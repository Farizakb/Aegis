"""Demo entrypoint: run the full agent graph with HITL web UI."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

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


async def _read_latest_incidents(redis: Redis, count: int) -> list[IncidentEvent]:
    entries = await redis.xrevrange(INCIDENTS_STREAM, count=count)
    return [IncidentEvent.model_validate_json(fields["incident"]) for _, fields in entries]


def promoted_incident(incident: IncidentEvent) -> IncidentEvent:
    """Derived identity for a promoted durable-fix run, so its report
    upserts a separate row instead of overwriting the mitigation run's."""
    return incident.model_copy(update={"incident_id": f"{incident.incident_id}#promo"})


async def main(count: int = 1) -> None:
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

    try:
        for incident in incidents:
            print(f"\nProcessing: {incident.title} ({incident.incident_id})")
            await graph.ainvoke(initial_state(incident))

        print("Waiting for durable-fix promotions (Ctrl+C to exit)...")
        while True:
            entry = await registry.next_promotion()
            incident = entry["incident"]
            print(f"\nPromoted durable fix for {incident.incident_id}")
            await graph.ainvoke(initial_state(
                promoted_incident(incident),
                plan=RemediationPlan(mitigation=entry["fix"]),
                triage=entry["triage"],
            ))
    finally:
        server.should_exit = True
        await server_task


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1)
    args = parser.parse_args()
    asyncio.run(main(args.count))

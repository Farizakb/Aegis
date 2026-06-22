"""Demo entrypoint: run the full agent graph with HITL web UI."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import uvicorn
from langchain_mcp_adapters.client import MultiServerMCPClient
from redis.asyncio import Redis

from agent.graph import build_graph
from agent.llm import get_propose_llm, get_triage_llm
from agent.state import AgentState, IncidentReport
from apply.applier import PatchApplier
from hitl.gate import ApprovalGate
from hitl.web import create_hitl_app
from policy.engine import PolicyEngine
from sandbox.executor import SandboxExecutor
from stream.consumer import INCIDENTS_STREAM
from stream.schema import IncidentEvent

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
REPO_ROOT = Path(__file__).resolve().parents[1]
HITL_PORT = int(os.environ.get("HITL_PORT", "8001"))


class PrintSink:
    def emit(self, report: IncidentReport) -> None:
        print(f"\n{'='*70}")
        print(f"REPORT: {report.incident_id} -> {report.outcome.value}")
        print(f"  retries={report.retries} sandbox_passed={report.sandbox_passed}")
        print(f"  policy={report.policy_decision} hitl={report.hitl_choice}")
        print(f"  tokens: in={report.total_input_tokens} out={report.total_output_tokens}")
        print(f"  latency: {report.total_latency_ms:.0f}ms")
        if report.applied_target_file:
            print(f"  applied to: {report.applied_target_file}")
        print("=" * 70)


async def _read_latest_incidents(redis: Redis, count: int) -> list[IncidentEvent]:
    entries = await redis.xrevrange(INCIDENTS_STREAM, count=count)
    return [IncidentEvent.model_validate_json(fields["incident"]) for _, fields in entries]


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
            }
        }
    )
    tools = await client.get_tools()
    search_tool = next(t for t in tools if t.name == "search_knowledge")

    gate = ApprovalGate()
    executor = SandboxExecutor(docker_client)
    policy_engine = PolicyEngine()
    applier = PatchApplier(repo_root=REPO_ROOT, backup_dir=REPO_ROOT / ".aegis_backups")
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
    )

    # Start HITL web UI in background
    hitl_app = create_hitl_app(gate)
    config = uvicorn.Config(hitl_app, host="0.0.0.0", port=HITL_PORT, log_level="warning")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    print(f"HITL approval UI running at http://localhost:{HITL_PORT}")

    try:
        for incident in incidents:
            print(f"\nProcessing: {incident.title} ({incident.incident_id})")
            result = await graph.ainvoke(
                {
                    "incident": incident,
                    "triage": None,
                    "retrieved_context": [],
                    "proposed_fix": None,
                    "retries": 0,
                    "sandbox_result": None,
                    "policy_verdict": None,
                    "hitl_decision": None,
                    "report": None,
                    "usage": [],
                }
            )
    finally:
        server.should_exit = True
        await server_task


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1)
    args = parser.parse_args()
    asyncio.run(main(args.count))

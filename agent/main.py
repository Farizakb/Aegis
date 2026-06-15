"""One-shot demo: read the latest incident(s) off aegis:incidents and run the agent graph."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from redis.asyncio import Redis

from agent.graph import build_graph
from agent.llm import get_llm
from agent.state import AgentState
from stream.consumer import INCIDENTS_STREAM
from stream.schema import IncidentEvent

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
REPO_ROOT = Path(__file__).resolve().parents[1]


async def _read_latest_incidents(redis: Redis, count: int) -> list[IncidentEvent]:
    entries = await redis.xrevrange(INCIDENTS_STREAM, count=count)
    return [IncidentEvent.model_validate_json(fields["incident"]) for _, fields in entries]


def _print_result(incident: IncidentEvent, result: AgentState) -> None:
    print("=" * 70)
    print(f"Incident: {incident.title} ({incident.incident_id})")
    print(f"  {incident.summary}")
    print()

    triage = result["triage"]
    print("Triage")
    print(f"  fault_kind : {triage.fault_kind.value}")
    print(f"  confidence : {triage.confidence:.2f}")
    print(f"  root cause : {triage.root_cause}")
    print(f"  reasoning  : {triage.reasoning}")
    print()

    print("Retrieved context")
    for chunk in result["retrieved_context"]:
        print(f"  [{chunk.score:.2f}] {chunk.source}")
    print()

    fix = result["proposed_fix"]
    print("Proposed fix")
    print(f"  target_file: {fix.target_file}")
    print(f"  {fix.description}")
    print(fix.patch)


async def main(count: int = 1) -> None:
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        incidents = await _read_latest_incidents(redis, count)
    finally:
        await redis.aclose()

    if not incidents:
        print("No incidents found on aegis:incidents.")
        return

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

    graph = build_graph(get_llm(), search_tool)

    for incident in incidents:
        result = await graph.ainvoke(
            {"incident": incident, "triage": None, "retrieved_context": [], "proposed_fix": None}
        )
        _print_result(incident, result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1)
    args = parser.parse_args()
    asyncio.run(main(args.count))

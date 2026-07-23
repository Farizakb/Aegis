"""Phase 5 demo: trigger a fault, run the pipeline, print the Jaeger trace URL.

Prereqs: `docker compose up -d redis postgres app consumer jaeger` and the agent
run (`python -m agent.main --count 1`) in another shell to approve via HITL.
This script triggers the fault and, after the incident is emitted, prints the
Jaeger trace URL derived from the traceparent on the incident stream entry.
"""

from __future__ import annotations

import asyncio
import os

import httpx
from redis.asyncio import Redis

from stream.consumer import INCIDENTS_STREAM

APP = os.environ.get("LIVE_APP_URL", "http://localhost:8000")
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
JAEGER_UI = os.environ.get("JAEGER_UI", "http://localhost:16686")


async def main(fault: str = "memory_leak") -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(f"{APP}/faults/{fault}/trigger")
        await asyncio.sleep(4)
        await client.post(f"{APP}/faults/{fault}/clear")

    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        for _ in range(20):
            entries = await redis.xrevrange(INCIDENTS_STREAM, count=1)
            if entries:
                _, fields = entries[0]
                tp = fields.get("traceparent", "")
                if tp:
                    trace_id = tp.split("-")[1]
                    print(f"Incident on stream. Jaeger trace:\n{JAEGER_UI}/trace/{trace_id}")
                    return
            await asyncio.sleep(2)
        print("No traced incident appeared within 40s.")
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())

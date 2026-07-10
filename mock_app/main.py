"""Mock target app: injectable faults that emit RawEvents onto the aegis:events stream."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

from mock_app.controls import FLAGS, SCALE
from mock_app.faults import FAULTS, Fault
from mock_app.faults.error_spike import ErrorSpikeFault
from stream.producer import Producer
from stream.schema import FaultKind

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
EMIT_INTERVAL_S = float(os.environ.get("EMIT_INTERVAL_S", "0.5"))


async def _emit_loop(producer: Producer) -> None:
    while True:
        await asyncio.sleep(EMIT_INTERVAL_S)
        for fault in FAULTS.values():
            if fault.is_active():
                await fault.emit_signals(producer)


class NullProducer:
    """Stream-disabled mode (sandbox containers): emit loop ticks, nothing is published."""

    async def publish(self, event) -> None:
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.environ.get("AEGIS_STREAM_DISABLED") == "1":
        app.state.producer = NullProducer()
        task = asyncio.create_task(_emit_loop(app.state.producer))
        try:
            yield
        finally:
            task.cancel()
        return

    from redis.asyncio import Redis

    redis = Redis.from_url(REDIS_URL)
    app.state.producer = Producer(redis)
    task = asyncio.create_task(_emit_loop(app.state.producer))
    try:
        yield
    finally:
        task.cancel()
        await redis.aclose()


app = FastAPI(title="Aegis mock app", lifespan=lifespan)
router = APIRouter(prefix="/faults", tags=["faults"])


@router.get("")
async def list_faults():
    return {kind.value: fault.is_active() for kind, fault in FAULTS.items()}


@router.post("/{kind}/trigger")
async def trigger_fault(kind: str):
    fault_kind, fault = _get_fault(kind)
    if fault.is_active():
        raise HTTPException(status_code=409, detail=f"{fault_kind.value} is already active")
    await fault.trigger()
    return {"kind": fault_kind.value, "active": fault.is_active()}


@router.post("/{kind}/clear")
async def clear_fault(kind: str):
    fault_kind, fault = _get_fault(kind)
    await fault.clear()
    return {"kind": fault_kind.value, "active": False}


def _get_fault(kind: str) -> tuple[FaultKind, Fault]:
    try:
        fault_kind = FaultKind(kind)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"unknown fault kind: {kind}")
    return fault_kind, FAULTS[fault_kind]


app.include_router(router)


class FlagUpdate(BaseModel):
    enabled: bool


class ScaleUpdate(BaseModel):
    workers: int = Field(ge=1, le=16)


@app.get("/flags")
async def list_flags():
    return FLAGS.all()


@app.post("/flags/{name}")
async def set_flag(name: str, body: FlagUpdate):
    try:
        FLAGS.set(name, body.enabled)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown flag: {name}")
    return {"name": name, "enabled": body.enabled}


@app.get("/scale")
async def get_scale():
    return {"workers": SCALE.workers}


@app.post("/scale")
async def set_scale(body: ScaleUpdate):
    SCALE.set_workers(body.workers)
    return {"workers": SCALE.workers}


@app.get("/healthz")
async def healthz():
    metrics: dict[str, float] = {}
    for fault in FAULTS.values():
        metrics.update(fault.metric_snapshot())
    return {
        "status": "ok",
        "workers": SCALE.workers,
        "flags": FLAGS.all(),
        "metrics": metrics,
    }


@app.get("/work")
async def work():
    error_spike = FAULTS[FaultKind.error_spike]
    assert isinstance(error_spike, ErrorSpikeFault)
    if error_spike.should_fail():
        return Response(status_code=500, content="internal error")
    return {"status": "ok"}

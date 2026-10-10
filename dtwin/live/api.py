"""
Live routes. The scenario name "live" also works on every existing
/api/{name}/... endpoint (state, series, timeline, whatif, report.pdf, ingest,
alerts): dtwin/api/app.py resolves it to the live buffer's twin, so the
dashboard needs no second code path.

    POST   /api/live/sample        one sample or {"samples": [...], "prewarm": bool, "period_s": s}
    GET    /api/live/status        freshness, source, sample age, speed, pause, prewarm boundary
    GET    /api/live/stream        Server-Sent Events: the full state after every sample,
                                   plus a heartbeat every 2 s so age and staleness stay current
    POST   /api/live/reset         empty the buffer and the injection list
    GET    /api/live/inject        the injection list
    POST   /api/live/inject        {"kind", "severity", "ramp_h", "channel"}
    DELETE /api/live/inject[/{id}] remove one or all
    GET    /api/live/sim-config    what the simulator polls: faults, pause, speed
    POST   /api/live/control       {"paused": bool, "speed": float}
"""

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from . import notify as live_notify
from .store import LiveStore

STORE = LiveStore()
router = APIRouter(prefix="/api/live")


def live_state(tw, t=None):
    s = tw.state(t)
    s["live"] = STORE.freshness()
    s["scenario"] = "live"
    return s


@router.post("/sample")
async def post_sample(request: Request):
    body = await request.json()
    if isinstance(body, dict) and "samples" in body:
        samples, prewarm, period = body["samples"], bool(body.get("prewarm")), body.get("period_s")
    else:
        samples, prewarm, period = (body if isinstance(body, list) else [body]), False, None
        if isinstance(body, dict):
            period = body.get("period_s")
    try:
        n = await asyncio.to_thread(STORE.add, samples, prewarm, period)
    except (ValueError, KeyError) as e:
        raise HTTPException(422, f"sample rejected: {e}")
    await asyncio.to_thread(live_notify.notify, STORE.twin)       # outbox only
    return {"accepted": n, "analysis_ms": STORE.analysis_ms, "version": STORE.version}


@router.get("/status")
def status():
    return STORE.freshness()


@router.post("/reset")
def reset():
    STORE.reset()
    live_notify.clear()
    return STORE.freshness()


@router.get("/inject")
def list_faults():
    return {"faults": STORE.sim_config()["faults"]}


@router.post("/inject")
async def inject(request: Request):
    b = await request.json()
    try:
        f = STORE.inject(b.get("kind"), b.get("severity", 0), b.get("ramp_h", 0), b.get("channel"))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return f


@router.delete("/inject")
def clear_all():
    STORE.clear()
    return {"faults": []}


@router.delete("/inject/{fid}")
def clear_one(fid: str):
    STORE.clear(fid)
    return {"faults": STORE.sim_config()["faults"]}


@router.get("/sim-config")
def sim_config():
    return STORE.sim_config()


@router.post("/control")
async def control(request: Request):
    b = await request.json()
    with STORE.lock:
        if "paused" in b:
            STORE.paused = bool(b["paused"])
        if b.get("speed"):
            STORE.speed = max(1.0, min(float(b["speed"]), 20000.0))
        STORE.version += 1
    return STORE.freshness()


@router.get("/stream")
async def stream(request: Request):
    async def gen():
        seen, beat = -1, 0.0
        while True:
            if await request.is_disconnected():
                break
            v = STORE.version
            if STORE.twin is not None and (v != seen or beat >= 2.0):
                payload = await asyncio.to_thread(lambda: live_state(STORE.twin))
                yield f"event: state\ndata: {json.dumps(payload, default=str)}\n\n"
                seen, beat = v, 0.0
            elif STORE.twin is None and beat >= 2.0:
                yield f"event: status\ndata: {json.dumps(STORE.freshness(), default=str)}\n\n"
                beat = 0.0
            await asyncio.sleep(0.25)
            beat += 0.25
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

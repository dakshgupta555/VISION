"""
GridTwin network backend, port 8001. Separate from the single-transformer
backend on :8000; it imports the existing engine read-only.

    uvicorn dtwin_network.app:app --port 8001
    python -m dtwin_network.sim                    # the fleet simulator, posts here

Routes (all under /api/net):
    GET  fleet                  the fleet file as parsed (positions flagged synthetic)
    GET  overview[?at=k]        map, kpis, what's happening, visit-first, events; at=k replays snapshot k
    GET  history                replay snapshots available
    GET  asset/{id}             one transformer: its own twin state + 48 h trend, or metadata + risk
    GET  gateway/{id}           devices, capacity, status, coverage assumption, cost
    POST uplink                 a gateway's forwarded batch (from the simulator)
    GET  sim-config             what the simulator polls: faults, cut links, gateways down, loss
    POST demo/inject | demo/link | demo/gateway | demo/reset | demo/control
    GET  plan?budget=&node_scale=&gw_scale=&radius_scale=    optimised + greedy + why
    GET  sensitivity?budget=    ±20 % on node cost, gateway cost, radius
    GET  strategies?budget=     A / B / C / D comparison
"""

import asyncio
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from . import planner, risk as riskmod, strategy, views
from .fleet import FleetError, load
from .store import NetworkStore

try:
    FLEET, FLEET_ERROR = load(), None
except (FleetError, OSError) as e:
    FLEET, FLEET_ERROR = None, f"config_network/fleet.yaml cannot be used: {e}"
STORE = NetworkStore(FLEET) if FLEET else None
_stop = threading.Event()


def _worker():
    while not _stop.is_set():
        try:
            STORE.analyse_all()
            STORE.tick()
        except Exception as e:                      # never let the worker die silently
            STORE.log("backend", f"analysis worker error: {type(e).__name__}: {e}", "warn")
        _stop.wait(0.25)


@asynccontextmanager
async def lifespan(app):
    if STORE:
        threading.Thread(target=_worker, daemon=True, name="net-analysis").start()
    yield
    _stop.set()


app = FastAPI(title="GridTwin network", lifespan=lifespan)


def need():
    if FLEET_ERROR:
        raise HTTPException(503, FLEET_ERROR)


def _budget(b):
    if b is None:
        return 400000.0
    if b < 0:
        raise HTTPException(422, "budget must be at least 0")
    return float(b)


@app.get("/api/net/fleet")
def fleet():
    need()
    return FLEET.to_json()


@app.get("/api/net/overview")
def overview(at: int = None):
    need()
    with STORE.lock:
        if at is None:
            return views.overview(STORE)
        snaps = list(STORE.snapshots)
        if not snaps:
            raise HTTPException(404, "no history yet")
        s = snaps[max(0, min(at, len(snaps) - 1))]
        ev = [e for e in STORE.events if e["n"] <= s["event_n"]][-60:][::-1]
        return {"replay": True, "clock": s["sim"], "devices": list(s["devices"].values()),
                "gateways": s["gateways"], "events": ev, "disclaimer": FLEET.district.get("disclaimer")}


@app.get("/api/net/history")
def history():
    need()
    with STORE.lock:
        return {"n": len(STORE.snapshots), "sim": [s["sim"] for s in STORE.snapshots]}


@app.get("/api/net/asset/{aid}")
def asset(aid: str):
    need()
    a = FLEET.assets.get(aid)
    if a is None:
        raise HTTPException(404, f"no transformer {aid!r} in the fleet")
    base = {"id": aid, "monitored": a.monitored, "rating_kva": a.rating_kva, "manufacturer": a.manufacturer,
            "cohort": a.cohort, "xy_km": list(a.xy), "metadata": a.meta, "gateway": a.gateway,
            "in_range": [g.id for g in FLEET.gateways.values() if FLEET.in_range(a, g)]}
    if not a.monitored:
        return {**base, "risk": riskmod.unmonitored(a)}
    with STORE.lock:
        d = STORE.devices[aid]
        tw, st = d.twin, d.state
        summ = STORE.summary(aid)
        rk = riskmod.monitored(a, summ)
    series = tw.series(None, hours=48.0) if tw is not None else None
    return {**base, "summary": summ, "state": st, "series": series, "risk": rk}


@app.get("/api/net/gateway/{gid}")
def gateway(gid: str):
    need()
    g = FLEET.gateways.get(gid)
    if g is None:
        raise HTTPException(404, f"no gateway {gid!r}")
    out = {"id": gid, "name": g.name, "existing": g.existing, "xy_km": list(g.xy), "capacity": g.capacity,
           "coverage_radius_km": g.radius_km, "cost_inr": g.cost_inr, "annual_inr": g.annual_inr,
           "coverage_assumption": f"{g.radius_km:g} km radius, a flat assumption for an urban LoRaWAN gateway; "
                                  "not a radio survey",
           "in_range": [a.id for a in FLEET.assets.values() if FLEET.in_range(a, g)]}
    if g.existing:
        with STORE.lock:
            gj = next(x for x in STORE.gateways_json() if x["id"] == gid)
            out.update(status=gj["status"], devices=[STORE.summary(d, brief=True) for d in gj["nodes"]],
                       used=gj["used"], last_batch=gj["last_batch"], waiting=gj["waiting"],
                       forwarded=gj["forwarded"], down_by_demo=gj["down_by_demo"])
    else:
        out.update(status="candidate site", devices=[], used=0)
    return out


@app.post("/api/net/uplink")
async def uplink(request: Request):
    need()
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(422, "uplink rejected: body is not JSON")
    try:
        return await asyncio.to_thread(STORE.uplink, body)
    except ValueError as e:
        raise HTTPException(422, f"uplink rejected: {e}")


@app.get("/api/net/sim-config")
def sim_config():
    need()
    return STORE.sim_config()


async def _body(request):
    try:
        return await request.json()
    except ValueError:
        raise HTTPException(422, "body is not JSON")


@app.post("/api/net/demo/inject")
async def demo_inject(request: Request):
    need()
    b = await _body(request)
    try:
        fid = STORE.inject(b.get("device"), b.get("kind"), b.get("severity", 1.0), b.get("ramp_h", 0.0), b.get("channel"))
    except (ValueError, TypeError) as e:
        raise HTTPException(422, str(e))
    return {"id": fid}


@app.post("/api/net/demo/link")
async def demo_link(request: Request):
    need()
    b = await _body(request)
    try:
        STORE.set_link(b.get("device"), bool(b.get("cut", True)))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return STORE.sim_config()


@app.post("/api/net/demo/gateway")
async def demo_gateway(request: Request):
    need()
    b = await _body(request)
    try:
        STORE.set_gateway(b.get("gateway"), bool(b.get("down", True)))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return STORE.sim_config()


@app.post("/api/net/demo/reset")
def demo_reset():
    need()
    with STORE.lock:
        STORE.reset()
        STORE.log("demo", "Demo reset: faults cleared, links restored, history re-sent by the simulator", "info")
    return STORE.sim_config()


@app.post("/api/net/demo/control")
async def demo_control(request: Request):
    need()
    b = await _body(request)
    with STORE.lock:
        if "paused" in b:
            STORE.demo["paused"] = bool(b["paused"])
        if "packet_loss" in b:
            p = float(b["packet_loss"])
            if not 0 <= p <= 0.9:
                raise HTTPException(422, "packet_loss must be between 0 and 0.9")
            STORE.demo["packet_loss"] = p
    return STORE.sim_config()


def _scales(node_scale, gw_scale, radius_scale):
    for v, n in ((node_scale, "node_scale"), (gw_scale, "gw_scale"), (radius_scale, "radius_scale")):
        if not 0.1 <= v <= 10:
            raise HTTPException(422, f"{n} must be between 0.1 and 10")
    return {"node_scale": node_scale, "gw_scale": gw_scale, "radius_scale": radius_scale}


@app.get("/api/net/plan")
def plan(budget: float = None, node_scale: float = 1.0, gw_scale: float = 1.0, radius_scale: float = 1.0):
    need()
    B, sc = _budget(budget), _scales(node_scale, gw_scale, radius_scale)
    risks = riskmod.static_risks(FLEET)
    t0 = time.perf_counter()
    opt = planner.solve(FLEET, B, risks=risks, **sc)
    gr = planner.greedy(FLEET, B, risks=risks, **sc)
    ms = round(1000 * (time.perf_counter() - t0), 1)
    why = planner.compare(opt, gr) if opt["feasible"] else opt["message"]
    return {"budget": B, "optimised": opt, "greedy": gr, "why": why, "solve_ms": ms, "risks": risks,
            "disclaimer": FLEET.district.get("disclaimer")}


@app.get("/api/net/geo")
def geo_view():
    """Positions, the distance matrix (metres) and the labels every synthetic
    thing must carry on screen."""
    need()
    m = planner.Model(FLEET)
    return {"fleet": FLEET.to_json(), "distance_m": {a: {g: round(v) for g, v in row.items()} for a, row in m.d.items()},
            "labels": {"positions": FLEET.district.get("positions_label"),
                       "coverage": FLEET.radio.get("coverage_label"),
                       "costs": FLEET.costs.get("label", "Illustrative costs"),
                       "link_budget": "estimate: " + str(FLEET.link_budget.get("model", "")),
                       "measured": "simulated packets, not a measurement"},
            "area": FLEET.district.get("area"), "origin_latlon": FLEET.district.get("origin_latlon"),
            "link_budget": FLEET.link_budget, "budget_inr": FLEET.planning.get("budget_inr", 400000)}


def _clean_overrides(ov):
    out = {}
    for gid, o in (ov or {}).items():
        if gid not in FLEET.gateways:
            raise HTTPException(422, f"unknown gateway {gid!r} in overrides")
        o = o or {}
        r = {}
        if o.get("radius_m") is not None:
            v = float(o["radius_m"])
            if not 100 <= v <= 20000:
                raise HTTPException(422, f"{gid}: radius_m must be between 100 and 20000")
            r["radius_m"] = v
        if o.get("cost_inr") is not None:
            v = float(o["cost_inr"])
            if not 0 <= v <= 1e8:
                raise HTTPException(422, f"{gid}: cost_inr must be between 0 and 10 crore")
            r["cost_inr"] = v
        if r:
            out[gid] = r
    return out


@app.post("/api/net/plan")
async def plan_post(request: Request):
    """The map's planner: budget, per-gateway radius/cost overrides and the
    link-budget switch. Returns optimised, greedy and current plans, coverage
    and every transformer x gateway pair with its separate fields."""
    need()
    b = await _body(request)
    B = _budget(b.get("budget"))
    ov = _clean_overrides(b.get("overrides"))
    lbon = b.get("link_budget")
    t0 = time.perf_counter()
    try:
        m = planner.Model(FLEET, overrides=ov, link_budget=lbon)
    except ValueError as e:
        raise HTTPException(422, str(e))
    risks = riskmod.static_risks(FLEET)
    opt = planner.solve(FLEET, B, risks=risks, model=m)
    gr = planner.greedy(FLEET, B, risks=risks, model=m)
    cur = planner.current(FLEET, risks=risks, model=m)
    show = opt if opt["feasible"] else cur
    load = {g["id"]: g["used"] for g in show["gateway_load"]}
    room = {g.id: (load.get(g.id, 0) < m.cap[g.id]) for g in m.G}
    pairs = {a.id: m.pairs_for(a.id, room) for a in m.T}
    ms = round(1000 * (time.perf_counter() - t0), 1)
    return {"budget": B, "optimised": opt, "greedy": gr, "current": cur,
            "why": planner.compare(opt, gr) if opt["feasible"] else opt["message"],
            "pairs": pairs, "radius_m": m.radius, "cost_inr": m.install_cost, "max_nodes": m.cap,
            "link_budget": m.use_lb, "solve_ms": ms, "risks": risks}


@app.get("/api/net/sensitivity")
def sensitivity(budget: float = None):
    need()
    return planner.sensitivity(FLEET, _budget(budget))


@app.get("/api/net/strategies")
def strategies(budget: float = None):
    need()
    return strategy.compare(FLEET, _budget(budget))

"""
FastAPI backend for the dashboard.

    uvicorn dtwin.api.app:app --reload --port 8000

Endpoints take a scenario name and an optional cursor `t` (sample index or ISO
timestamp). Without `t` they answer for the latest sample. The cursor is how
replay works: the frontend moves it, the twin answers as of that instant.
Truth files are never read here.
"""

from typing import Optional, Union

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from .engine import get_twin, scenarios, selftest
from .. import calibrate
from ..live.store import SESSION as _LIVE_SESSION
from ..physics.nameplate import IS_1180_RATINGS

def _load_dotenv(path=Path(__file__).resolve().parents[2] / ".env"):
    """Read KEY=VALUE lines from the repo's .env (gitignored) into the
    environment, without overriding anything already set."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip().removeprefix("export ").strip(), v.strip().strip("'").strip('"')
        os.environ.setdefault(k, v)


_load_dotenv()

app = FastAPI(title="Distribution Transformer Digital Twin", version="0.3")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["X-Dtwin-Session"])


class _SessionHeader:
    """Every response carries the backend's session id (pure ASGI, so the SSE
    stream is not buffered). A source that sees it change re-sends its history."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_h(msg):
            if msg["type"] == "http.response.start":
                msg.setdefault("headers", [])
                msg["headers"] = list(msg["headers"]) + [(b"x-dtwin-session", _LIVE_SESSION.encode())]
            await send(msg)
        return await self.app(scope, receive, send_h)


app.add_middleware(_SessionHeader)


def _twin(name, kva=None):
    if name == "live":                                  # the live buffer, not a file
        from ..live.api import STORE
        if STORE.twin is None:
            raise HTTPException(503, "no live data yet: start  python -m dtwin.live")
        return STORE.twin
    if name not in scenarios():
        raise HTTPException(404, f"unknown scenario {name!r}")
    cal = calibrate.active()
    if cal is None:
        return get_twin(name, kva)
    try:                                                 # a report applies to every file it matches
        return get_twin(name, kva, calibration=cal)
    except calibrate.ReportError as e:
        raise HTTPException(409, f"the applied test report does not fit {name}: {e}. Reset it or apply another.")


def _cursor(t: Optional[str]) -> Union[int, str, None]:
    if t is None or t == "":
        return None
    return int(t) if t.lstrip("-").isdigit() else t


# --------------------------------------------------------------- calibration
# One factory test report may be applied at a time; it calibrates every twin
# the API builds until it is reset. With none applied every answer is the
# built-in twin, byte for byte (check_calibration.py).
def _report_from(body):
    if not isinstance(body, dict):
        raise HTTPException(422, "body must be a JSON object: {report} or {text}")
    if body.get("text") is not None:
        try:
            return calibrate.parse_text(str(body["text"]))
        except calibrate.ReportError as e:
            raise HTTPException(422, str(e))
    return body.get("report")


async def _json(request):
    try:
        return await request.json()
    except ValueError:
        raise HTTPException(422, "body is not JSON")


@app.get("/api/calibration")
def calibration_status():
    cal = calibrate.active()
    return {"active": cal is not None, **({"result": cal.as_json(), "report": cal.report} if cal else {})}


@app.get("/api/calibration/samples")
def calibration_samples():
    out = []
    for label, p in calibrate.SAMPLES.items():
        out.append({"label": label, "file": p.name, "text": p.read_text(), "report": calibrate.load(p)})
    return {"samples": out, "builtin": calibrate.builtin_report(),
            "model_reference_temp_c": calibrate.MODEL_REF_TEMP_C}


@app.post("/api/calibration/preview")
async def calibration_preview(request: Request):
    """Calibrate without applying: the table and checks for a report."""
    try:
        return calibrate.Calibration(_report_from(await _json(request))).as_json()
    except calibrate.ReportError as e:
        raise HTTPException(422, str(e))


@app.post("/api/calibration")
async def calibration_apply(request: Request):
    """Apply a report. With {"scenario": name} it is first checked against that
    data file (voltages must match) and the twin is built once to prove it."""
    body = await _json(request)
    try:
        cal = calibrate.Calibration(_report_from(body))
    except calibrate.ReportError as e:
        raise HTTPException(422, str(e))
    scen = body.get("scenario")
    if scen and scen != "live":
        if scen not in scenarios():
            raise HTTPException(404, f"unknown scenario {scen!r}")
        try:
            get_twin(scen, None, calibration=cal)
        except calibrate.ReportError as e:
            raise HTTPException(422, str(e))
    calibrate.apply(cal)
    _DISPATCHERS.clear()
    return {"active": True, "result": cal.as_json()}


@app.delete("/api/calibration")
def calibration_reset():
    calibrate.reset()
    _DISPATCHERS.clear()
    return {"active": False}


@app.get("/api/scenarios")
def list_scenarios():
    return {"scenarios": scenarios(), "default": "showcase"}


@app.get("/api/{name}/state")
def state(name: str, t: Optional[str] = None, kva: Optional[float] = None):
    if name == "live":
        from ..live.api import live_state
        return live_state(_twin(name), _cursor(t))
    return _twin(name, kva).state(_cursor(t))


@app.get("/api/{name}/series")
def series(name: str, t: Optional[str] = None, hours: float = 48.0, kva: Optional[float] = None):
    return _twin(name, kva).series(_cursor(t), hours=hours)


@app.get("/api/{name}/timeline")
def timeline(name: str, kva: Optional[float] = None):
    return _twin(name, kva).timeline()


@app.get("/api/{name}/whatif")
def whatif(name: str, t: Optional[str] = None, load: Optional[float] = None,
           ambient: Optional[float] = None, cf: Optional[float] = None,
           kva: Optional[float] = None):
    tw = _twin(name, kva)
    return tw.whatif(tw.locate(_cursor(t)), load_pu=load, ambient_c=ambient, cooling_factor=cf)


@app.get("/api/selftest")
def run_selftest(kva: float = 100.0):
    return selftest(float(kva))


@app.get("/api/ratings")
def ratings():
    return {"ratings": [25, 63, 100, 250], "all_is1180": IS_1180_RATINGS}


@app.get("/api/{name}/ingest")
def ingest_report(name: str, kva: Optional[float] = None):
    rep = _twin(name, kva).report
    return {**rep.as_dict(), "text": rep.text()}


@app.get("/api/{name}/report.pdf")
def report_pdf(name: str, t: Optional[str] = None, kva: Optional[float] = None):
    from ..report import build_pdf
    tw = _twin(name, kva)
    pdf = build_pdf(tw, _cursor(t))
    stamp = tw.rows.index[tw.locate(_cursor(t))].strftime("%Y%m%d_%H%M")
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{tw.np_.tag}_{name}_{stamp}.pdf"'})


_DISPATCHERS = {}


def _dispatcher(name, kva):
    """Replay (or live-outbox) dispatchers only. The API has no way to construct
    a live-sending one: scrubbing history can never reach a real recipient."""
    if name == "live":                       # the live twin changes every sample: never cached
        from ..live import notify as live_notify
        return live_notify.dispatcher(_twin(name))
    cal = calibrate.active()
    key = (name, kva) if cal is None else (name, kva, cal.key)
    if key not in _DISPATCHERS:
        from ..notify.dispatch import Dispatcher
        _DISPATCHERS[key] = Dispatcher(_twin(name, kva), mode="replay")
    return _DISPATCHERS[key]


@app.get("/api/{name}/notifications")
def notifications(name: str, t: Optional[str] = None, kva: Optional[float] = None, limit: int = 40):
    if name == "live":                       # same policy, outbox only; built on the live twin as it is now
        from ..live import notify as live_notify
        tw = _twin(name)
        d = live_notify.dispatcher(tw)
        d.process(tw.rows.index[-1])
        upto = tw.rows.index[-1]
        return {**d.status(), "upto": upto.isoformat(), "entries": d.feed(upto, limit),
                "total_planned": len(d.events), "episodes": len(tw.alerts)}
    tw = _twin(name, kva)
    d = _dispatcher(name, kva)
    upto = tw.rows.index[tw.locate(_cursor(t))]
    d.process(upto)
    return {**d.status(), "upto": upto.isoformat(), "entries": d.feed(upto, limit),
            "total_planned": len(d.events), "episodes": len(tw.alerts)}


@app.get("/api/{name}/outbox/{file}")
def outbox_file(name: str, file: str, kva: Optional[float] = None):
    folder = _dispatcher(name, kva).outbox.folder / "pdf"
    p = folder / Path(file).name                    # no path traversal
    if not p.exists():
        raise HTTPException(404, "attachment not rendered yet")
    return FileResponse(p, media_type="application/pdf", filename=p.name)


@app.get("/api/demo/status")
def demo_status():
    from ..notify import demo
    from ..notify.policy import load_config
    return demo.status(load_config())


@app.post("/api/{name}/demo-send/{msg_id:path}")
def demo_send(name: str, msg_id: str, kva: Optional[float] = None):
    """Explicit, human-triggered: send one replayed event for real to the demo
    recipients. The automatic replay path never reaches this."""
    from ..notify import demo
    d = _dispatcher(name, kva)
    try:
        sent = demo.send_event(d, msg_id)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(502, f"SMTP send failed: {e}")
    return {"sent": sent, **demo.status(d.cfg)}


@app.get("/api/{name}/components")
def components(name: str, t: Optional[str] = None, kva: Optional[float] = None):
    """3D twin component states and inspector records at the cursor."""
    from ..twin.diagnose import diagnose
    tw = _twin(name, kva)
    fresh = None
    if name == "live":
        from ..live.api import STORE
        fresh = STORE.freshness()
    return diagnose(tw, tw.locate(_cursor(t)), fresh)["components"]


@app.get("/api/{name}/diagnose")
def diagnose_route(name: str, t: Optional[str] = None, kva: Optional[float] = None):
    """Component groups, the evidence for each, and ranked hypotheses."""
    from ..twin.diagnose import diagnose
    tw = _twin(name, kva)
    fresh = None
    if name == "live":
        from ..live.api import STORE
        fresh = STORE.freshness()
    d = diagnose(tw, tw.locate(_cursor(t)), fresh)
    d.pop("components")
    return d


@app.post("/api/{name}/simulate")
async def simulate_route(name: str, request: Request, kva: Optional[float] = None):
    """An isolated what-if on a snapshot of the observed state at the cursor.
    Never writes to the observed data."""
    from ..twin.simulate import simulate
    body = await request.json()
    tw = _twin(name, kva)
    i = tw.locate(_cursor(str(body["t"])) if body.get("t") is not None else None)
    return simulate(tw, i, body.get("inputs") or {}, horizon_h=float(body.get("horizon_h", 24)))


@app.get("/api/{name}/fem")
def fem_route(name: str, t: Optional[str] = None, kva: Optional[float] = None):
    """Per-phase FEM winding temperature field at the cursor."""
    from ..twin.fem_view import at_cursor
    tw = _twin(name, kva)
    return at_cursor(tw, tw.locate(_cursor(t)))


@app.post("/api/{name}/fem/simulate")
async def fem_simulate_route(name: str, request: Request, kva: Optional[float] = None):
    """Baseline, simulated and difference FEM fields for a what-if. Read-only."""
    from ..twin.fem_view import what_if
    body = await request.json()
    tw = _twin(name, kva)
    i = tw.locate(_cursor(str(body["t"])) if body.get("t") is not None else None)
    return what_if(tw, i, body.get("inputs") or {})


@app.get("/api/{name}/curves")
def curves_route(name: str, kva: Optional[float] = None, n1: Optional[float] = None, area_cm2: Optional[float] = None,
                 path_m: Optional[float] = None, gap_mm: Optional[float] = None, load_pct: float = 100.0,
                 pf: float = 0.9, lagging: bool = True):
    """B-H, excitation (V-I) and voltage-transfer characteristics. Design
    parameters are illustrative and can be overridden."""
    from ..twin.engineering import curves
    tw = _twin(name, kva)
    design = {"n1_turns": n1, "a_core_m2": None if area_cm2 is None else area_cm2 / 1e4,
              "l_core_m": path_m, "gap_mm": gap_mm}
    return curves(tw.np_, design, load_pct=load_pct, pf=pf, lagging=lagging)


@app.get("/api/{name}/losses")
def losses_route(name: str, t: Optional[str] = None, kva: Optional[float] = None, pf: float = 0.9):
    from ..twin.engineering import losses
    tw = _twin(name, kva)
    return losses(tw, tw.locate(_cursor(t)), pf=pf)


@app.get("/api/{name}/agents")
def agents_route(name: str, t: Optional[str] = None, kva: Optional[float] = None):
    """Structured output of the observation, physics, diagnostic and
    recommendation agents (deterministic; templates filled with numbers)."""
    from ..agents import run_agents
    tw = _twin(name, kva)
    fresh = None
    if name == "live":
        from ..live.api import STORE
        fresh = STORE.freshness()
    return run_agents(tw, tw.locate(_cursor(t)), fresh)


@app.get("/api/{name}/explain/{metric}")
def explain_route(name: str, metric: str, t: Optional[str] = None, kva: Optional[float] = None):
    """Calculation trace behind one Overview tile: equation, substituted
    values, model sweep, sensitivity, provenance. Read-only."""
    from ..twin.explain import METRICS, explain
    if metric not in METRICS:
        raise HTTPException(404, f"unknown metric {metric!r}; one of {', '.join(METRICS)}")
    tw = _twin(name, kva)
    return explain(tw, tw.locate(_cursor(t)), metric)


@app.get("/api/{name}/alerts")
def alerts(name: str):
    from .engine import _alert_json
    return {"alerts": [_alert_json(a) for a in _twin(name).alerts]}


from ..live.api import router as _live_router   # noqa: E402  (after the generic routes on purpose)
app.include_router(_live_router)

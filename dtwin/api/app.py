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

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from .engine import get_twin, scenarios, selftest
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
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _twin(name, kva=None):
    if name == "live":                                  # the live buffer, not a file
        from ..live.api import STORE
        if STORE.twin is None:
            raise HTTPException(503, "no live data yet: start  python -m dtwin.live --speed 600")
        return STORE.twin
    if name not in scenarios():
        raise HTTPException(404, f"unknown scenario {name!r}")
    return get_twin(name, kva)


def _cursor(t: Optional[str]) -> Union[int, str, None]:
    if t is None or t == "":
        return None
    return int(t) if t.lstrip("-").isdigit() else t


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
    """Replay dispatchers only. The API has no way to construct a live one:
    scrubbing history can never reach a real recipient."""
    key = (name, kva)
    if key not in _DISPATCHERS:
        from ..notify.dispatch import Dispatcher
        _DISPATCHERS[key] = Dispatcher(_twin(name, kva), mode="replay")
    return _DISPATCHERS[key]


@app.get("/api/{name}/notifications")
def notifications(name: str, t: Optional[str] = None, kva: Optional[float] = None, limit: int = 40):
    if name == "live":
        return {"mode": "live-simulated", "entries": [], "total_planned": 0, "episodes": 0,
                "outbox": "", "smtp_configured": False, "sms_configured": False,
                "guard": "notifications are not generated from the live simulator stream"}
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


@app.get("/api/{name}/alerts")
def alerts(name: str):
    from .engine import _alert_json
    return {"alerts": [_alert_json(a) for a in _twin(name).alerts]}


from ..live.api import router as _live_router   # noqa: E402  (after the generic routes on purpose)
app.include_router(_live_router)

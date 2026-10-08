"""
FastAPI backend for the dashboard.

    uvicorn dtwin.api.app:app --reload --port 8000

Endpoints take a scenario name and an optional cursor `t` (sample index or ISO
timestamp). Without `t` they answer for the latest sample. The cursor is how
replay works: the frontend moves it, the twin answers as of that instant.
Truth files are never read here.
"""

from typing import Optional, Union

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .engine import get_twin, scenarios, selftest
from ..physics.nameplate import IS_1180_RATINGS

app = FastAPI(title="Distribution Transformer Digital Twin", version="0.3")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _twin(name, kva=None):
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


@app.get("/api/{name}/alerts")
def alerts(name: str):
    from .engine import _alert_json
    return {"alerts": [_alert_json(a) for a in _twin(name).alerts]}

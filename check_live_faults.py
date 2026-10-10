"""
Final check, live mode: inject every fault the Inject panel offers, one per
isolated backend (each with its own simulator and dashboard), at the default
120x, and report what the twin did.

    python check_live_faults.py [--only cooling_degradation,overload] [--speed 120]

For each fault: a fresh backend and simulator (48 h of healthy prewarm), two
live samples, then the injection with the panel's default severity and ramp.
Polls the live state until an alert of the injected family appears (or a
48 transformer-hour timeout), freezes the stream, records the alert, the
components whose colour changed and the exact "What's happening" text the
dashboard renders, then clears the fault and measures how long the alarm
takes to clear (36 transformer-hour timeout).

Times are transformer-hours of record time, from the sample current when the
fault was injected to the sample at which the alert was first seen live.
Every process started here is killed on exit, error or Ctrl-C.
Takes about 45 minutes of wall time at 120x. Results: results/live_faults.json
and screens/live_<kind>.png.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Exactly the Inject panel's choices and defaults (dashboard/src/live.jsx FAULTS).
FAULTS = [
    {"kind": "cooling_degradation", "label": "Cooling degradation", "severity": 0.3, "ramp_h": 24, "family": "asset"},
    {"kind": "oil_loss", "label": "Oil loss", "severity": 0.3, "ramp_h": 72, "family": "asset"},
    {"kind": "overload", "label": "Overload", "severity": 1.25, "ramp_h": 1, "family": "asset"},
    {"kind": "unbalance_growth", "label": "Phase unbalance", "severity": 0.12, "ramp_h": 24, "family": "asset"},
    {"kind": "sensor_drift", "label": "Oil probe drift", "severity": 5, "ramp_h": 24, "channel": "oil_temp", "family": "sensor"},
    {"kind": "sensor_stuck", "label": "Stuck current channel", "severity": 1, "ramp_h": 0, "channel": "IY", "family": "sensor"},
    {"kind": "sensor_dead", "label": "Dead current channel", "severity": 1, "ramp_h": 0, "channel": "IB", "family": "sensor"},
]
DETECT_H, CLEAR_H = 48.0, 36.0
PROCS = []


def http(port, path, body=None, method=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method or ("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def spawn(cmd, cwd, env=None, log=None):
    p = subprocess.Popen(cmd, cwd=cwd, env={**os.environ, **(env or {})}, start_new_session=True,
                         stdout=open(log, "w") if log else subprocess.DEVNULL, stderr=subprocess.STDOUT)
    PROCS.append(p)
    return p


def kill_all():
    for p in PROCS:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for p in PROCS:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)


def hours(a, b):
    from datetime import datetime
    return round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 3600, 2)


def run_one(k, f, speed, tmp, out):
    be, vp = 8801 + k, 5191 + k
    rec = {"fault": f["label"], "kind": f["kind"], "severity": f["severity"], "ramp_h": f["ramp_h"],
           "channel": f.get("channel"), "expected_family": f["family"]}
    out[f["kind"]] = rec
    spawn([sys.executable, "-m", "uvicorn", "dtwin.api.app:app", "--port", str(be)], ROOT, log=tmp / f"be_{be}.log")
    spawn(["npx", "vite", "--port", str(vp), "--strictPort"], ROOT / "dashboard",
          env={"DTWIN_API": f"http://127.0.0.1:{be}"}, log=tmp / f"vite_{vp}.log")
    for _ in range(120):
        try:
            http(be, "/api/scenarios")
            break
        except OSError:
            time.sleep(1)
    spawn([sys.executable, "-m", "dtwin.live", "--api", f"http://127.0.0.1:{be}", "--speed", str(speed)], ROOT,
          log=tmp / f"sim_{be}.log")
    while True:                                          # prewarm in, and two live samples on top
        st = http(be, "/api/live/status")
        if st.get("has_data") and not st["history"]["rebuilding"] and st.get("sample_age_s") is not None \
                and st["n_samples"] >= st["history"]["need"] + 2:
            break
        time.sleep(1)
    base = http(be, "/api/live/state")
    comps0 = {c["id"]: c["state"] for c in http(be, "/api/live/components")["components"]}
    rec["alerts_before_injection"] = [a["what"] for a in base["alerts_live"]]
    inj = http(be, "/api/live/inject", {k2: f[k2] for k2 in ("kind", "severity", "ramp_h") if k2 in f}
               | {"channel": f.get("channel")})
    t_inj = inj["injected_at_sample"]
    rec["injected_at"] = t_inj
    first_any, hit = None, None
    while True:
        s = http(be, "/api/live/state")
        now = s["live"]["last_sample"]
        new = [a for a in s["alerts_live"] if a["what"] not in rec["alerts_before_injection"]]
        if new and first_any is None:
            first_any = {"what": new[0]["what"], "family": new[0]["family"], "after_h": hours(t_inj, now)}
        mine = [a for a in new if a["family"] == f["family"]]
        if mine:
            hit = (mine[0], s, now)
            break
        if hours(t_inj, now) > DETECT_H:
            break
        time.sleep(2)
    rec["first_alert"] = first_any
    if hit is None:
        rec.update(detected=False)
    else:
        a, s, now = hit
        http(be, "/api/live/control", {"paused": True})
        time.sleep(1)
        s = http(be, "/api/live/state")
        comps = http(be, "/api/live/components")
        changed = [{"id": c["id"], "name": c["name"], "from": comps0.get(c["id"]), "to": c["state"], "why": c["reason"]}
                   for c in comps["components"] if c["state"] != comps0.get(c["id"])]
        ui = subprocess.run(["node", "scripts/live_whats.mjs", "--url", f"http://localhost:{vp}",
                             "--out", f"../screens/live_{f['kind']}.png"], cwd=ROOT / "dashboard",
                            capture_output=True, text=True, timeout=180)
        try:
            ui = json.loads(ui.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            ui = {"error": ui.stderr[-500:]}
        rec.update(detected=True, alert=a["what"], family=a["family"], level=a["level"],
                   detect_h=hours(t_inj, now), alert_episode_start_h=hours(t_inj, a["start"]),
                   all_live=[x["what"] for x in s["alerts_live"]],
                   recommendations=[x["text"] + (" " + x["detail"] if x.get("detail") else "") for x in s["recommendations"]],
                   newest_fault=comps.get("newest_fault"), colour_changes=changed, ui=ui,
                   health=s["health"]["index"], physical_limit=s["physical_limit"]["beyond"])
        http(be, "/api/live/control", {"paused": False})
    http(be, "/api/live/inject", method="DELETE")
    t_clr = http(be, "/api/live/state")["live"]["last_sample"]
    while True:
        s = http(be, "/api/live/state")
        now = s["live"]["last_sample"]
        if not [a for a in s["alerts_live"] if a["what"] not in rec["alerts_before_injection"]]:
            rec["clear_after_h"] = hours(t_clr, now)
            break
        if hours(t_clr, now) > CLEAR_H:
            rec["clear_after_h"] = None
            rec["still_active_at_timeout"] = [a["what"] for a in s["alerts_live"]]
            break
        time.sleep(2)
    rec["done"] = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--speed", type=float, default=120.0)
    a = ap.parse_args()
    faults = [f for f in FAULTS if not a.only or f["kind"] in a.only.split(",")]
    tmp = Path(tempfile.mkdtemp(prefix="live_faults_"))
    print(f"process logs in {tmp}", flush=True)
    out = {}
    try:
        th = [threading.Thread(target=lambda k=k, f=f: _safe(run_one, k, f, a.speed, tmp, out), daemon=True)
              for k, f in enumerate(faults)]
        for t in th:
            t.start()
            time.sleep(3)
        for t in th:
            t.join()
    finally:
        kill_all()
        (ROOT / "results" / "live_faults.json").write_text(json.dumps({"speed": a.speed, "faults": out}, indent=1))
    for kind, r in out.items():
        print(f"{r['fault']:24s} detected={r.get('detected')} family={r.get('family')} "
              f"after={r.get('detect_h')} h  alert={r.get('alert')}  clear={r.get('clear_after_h')} h")
    return 0


def _safe(fn, *args):
    try:
        fn(*args)
    except Exception as e:                       # one fault failing must not hide the others
        args[-1].setdefault(args[1]["kind"], {})["error"] = f"{type(e).__name__}: {e}"


if __name__ == "__main__":
    sys.exit(main())

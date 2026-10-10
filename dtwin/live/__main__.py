"""
Live simulator: steps the transformer physics one sample at a time, adds sensor
noise, and POSTs each sample to the backend, exactly as the ESP32 device will
(same JSON contract, source "sensor" instead of "simulator").

    python -m dtwin.live --speed 600              # 15-min samples, one every 1.5 s
    python -m dtwin.live --speed 3600 --prewarm-h 48 --api http://127.0.0.1:8000

Prewarm: the previous 48 h are generated and sent at once, so the 24 h rolling
fit and every detector are ready from the first live second. The dashboard
marks the prewarm period.

Faults injected from the dashboard are read from /api/live/sim-config every
step and change this process's PHYSICS. The backend analytics only ever see
the samples.
"""

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta

from .sim import LiveSimulator


def post(api, path, body):
    req = urllib.request.Request(api + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def get(api, path):
    with urllib.request.urlopen(api + path, timeout=10) as r:
        return json.loads(r.read())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--speed", type=float, default=600.0, help="simulated seconds per real second")
    ap.add_argument("--interval-min", type=float, default=15.0, help="sample interval, simulated minutes")
    ap.add_argument("--prewarm-h", type=float, default=48.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--season", default="summer")
    ap.add_argument("--no-reset", action="store_true", help="keep the backend's existing buffer")
    a = ap.parse_args(argv)

    sim = LiveSimulator(start=datetime.now() - timedelta(hours=a.prewarm_h), interval_min=a.interval_min,
                        seed=a.seed, season=a.season)
    try:
        if not a.no_reset:
            post(a.api, "/api/live/reset", {})
        post(a.api, "/api/live/control", {"speed": a.speed, "paused": False})
    except OSError as e:
        print(f"backend not reachable at {a.api}: {e}\nstart it with: uvicorn dtwin.api.app:app --port 8000")
        return 1
    n_pre = int(a.prewarm_h * 60 / a.interval_min)
    pre = [dict(sim.step(), source="simulator") for _ in range(n_pre)]
    r = post(a.api, "/api/live/sample", {"samples": pre, "prewarm": True})
    print(f"prewarm: {n_pre} samples ({a.prewarm_h:g} h) sent, analysis {r['analysis_ms']} ms. Streaming...")

    sent = 0
    while True:
        try:
            cfg = get(a.api, "/api/live/sim-config")
        except OSError:
            time.sleep(1.0)
            continue
        speed = float(cfg.get("speed") or a.speed)
        period = a.interval_min * 60.0 / speed
        if cfg.get("paused"):
            time.sleep(0.5)
            continue
        sim.set_faults(cfg.get("faults", []))
        t0 = time.time()
        s = dict(sim.step(), source="simulator", period_s=round(period, 3))
        try:
            post(a.api, "/api/live/sample", s)
        except OSError as e:
            print(f"post failed: {e}")
        sent += 1
        if sent % 20 == 0:
            act = ", ".join(f"{f['kind']}" for f in cfg.get("faults", [])) or "none"
            print(f"  {s['timestamp']}  sent {sent}  oil {s['oil_temp']} C  IR {s['IR']} A  faults: {act}")
        time.sleep(max(0.0, period - (time.time() - t0)))


if __name__ == "__main__":
    sys.exit(main())

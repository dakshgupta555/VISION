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
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timedelta

from .sim import LiveSimulator

TIMEOUT_S = 5.0
RETRIES = 3                 # after the first attempt, with backoff 0.5, 1, 2 s


def _call(api, path, body=None, timeout=TIMEOUT_S):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(api + path, data=data, method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def call(api, path, body=None, timeout=TIMEOUT_S, retries=RETRIES):
    """One request with retries and backoff. Raises the last OSError. A 4xx
    is not retried: the sample itself is wrong and resending will not help."""
    for k in range(retries + 1):
        try:
            return _call(api, path, body, timeout)
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                raise
            err = e
        except OSError as e:
            err = e
        if k < retries:
            time.sleep(0.5 * 2 ** k)
    raise err


def post(api, path, body, timeout=TIMEOUT_S):
    return call(api, path, body, timeout)


def get(api, path):
    return call(api, path)


class Link:
    """Tracks the backend session. When it changes (backend restarted, buffer
    empty) the history window is sent again as prewarm so the detectors are
    ready at once instead of rebuilding for 48 h. Reports trouble in one line
    when it starts and one when it ends."""

    def __init__(self, api, history, speed, log=lambda m: print(m, flush=True)):
        self.api, self.history, self.speed, self.log = api, history, speed, log
        self.session, self.down = None, False
        self.rejected = self.resent = 0

    def _seen(self, reply):
        sid = reply.get("session") if isinstance(reply, dict) else None
        if sid and self.session and sid != self.session:
            try:
                self.reprewarm(sid)
            except OSError as e:                  # session left unchanged: tried again next reply
                self.fail("re-prewarm", e)
                return
            self.session = sid
        elif sid:
            self.session = sid

    def reprewarm(self, sid):
        pre = [dict(x, source="simulator") for x in self.history]
        call(self.api, "/api/live/control", {"speed": self.speed, "paused": False})
        r = call(self.api, "/api/live/sample", {"samples": pre, "prewarm": True}, timeout=30.0)
        self.resent += 1
        self.log(f"backend restarted (new session {sid}): re-sent {len(pre)} samples of history "
                 f"as prewarm, analysis {r.get('analysis_ms')} ms")

    def ok(self):
        if self.down:
            self.log("backend reachable again")
        self.down = False

    def fail(self, what, e):
        if not self.down:
            self.log(f"backend unreachable at {self.api} ({what}: {e}); retrying each sample")
        self.down = True

    def config(self):
        try:
            c = get(self.api, "/api/live/sim-config")
        except OSError as e:
            self.fail("sim-config", e)
            return None
        self.ok()
        self._seen(c)
        return c

    def send(self, sample):
        self.history.append({k: v for k, v in sample.items() if k != "period_s"})
        try:
            r = post(self.api, "/api/live/sample", sample)
        except urllib.error.HTTPError as e:
            self.rejected += 1
            self.log(f"sample rejected by the backend: HTTP {e.code} {e.read()[:200]!r}")
            return None
        except OSError as e:
            self.fail("sample", e)
            return None
        self.ok()
        self._seen(r)
        return r


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
    history = deque(maxlen=n_pre)
    pre = [dict(sim.step(), source="simulator") for _ in range(n_pre)]
    history.extend(pre)
    r = post(a.api, "/api/live/sample", {"samples": pre, "prewarm": True}, timeout=30.0)
    link = Link(a.api, history, a.speed)
    link.session = r.get("session")
    print(f"prewarm: {n_pre} samples ({a.prewarm_h:g} h) sent, analysis {r['analysis_ms']} ms, "
          f"session {link.session}. Streaming...", flush=True)

    sent = 0
    period = a.interval_min * 60.0 / a.speed
    while True:
        cfg = link.config()
        if cfg is None:
            time.sleep(max(1.0, period))
            continue
        speed = float(cfg.get("speed") or a.speed)
        link.speed = speed
        period = a.interval_min * 60.0 / speed
        if cfg.get("paused"):
            time.sleep(0.5)
            continue
        sim.set_faults(cfg.get("faults", []))
        t0 = time.time()
        s = dict(sim.step(), source="simulator", period_s=round(period, 3))
        link.send(s)
        sent += 1
        if sent % 20 == 0:
            act = ", ".join(f"{f['kind']}" for f in cfg.get("faults", [])) or "none"
            print(f"  {s['timestamp']}  sent {sent}  oil {s['oil_temp']} C  IR {s['IR']} A  faults: {act}", flush=True)
        time.sleep(max(0.0, period - (time.time() - t0)))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("simulator stopped", flush=True)

"""
Live stream survives a backend restart.

Starts a real backend on a spare port and the real simulator process against
it, lets it stream past the prewarm, kills the backend, starts a new one, and
keeps the same simulator streaming. Passes when:

  1. a single sample into an empty buffer is accepted (200, not 422)
  2. a malformed sample is still rejected (422)
  3. the simulator saw zero rejected samples
  4. it noticed the new session and re-sent its history as prewarm
  5. after the restart the buffer holds the full 48 h and analysis runs
     (no analysis_error, history not rebuilding, state served)
  6. every response carries the session header
  7. no simulator or test backend is left running

    python check_restart.py
"""

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

PORT = 8011
API = f"http://127.0.0.1:{PORT}"
ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(ROOT, ".tmp", "check_restart_sim.log")
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def req(path, body=None, timeout=10):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(API + path, data=data, method="GET" if body is None else "POST",
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.status, dict(resp.headers), json.loads(resp.read())


def backend():
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "dtwin.api.app:app", "--port", str(PORT)],
                         cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            req("/api/live/status")
            return p
        except OSError:
            time.sleep(0.2)
    p.kill()
    raise SystemExit("test backend did not start")


def wait_for(pred, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            v = pred()
            if v:
                return v
        except OSError:
            pass
        time.sleep(0.3)
    return None


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    print("Live restart robustness")
    be = backend()
    sim = None
    try:
        # 1-2: the contract on an empty buffer
        try:
            code, hdr, r = req("/api/live/sample", {"timestamp": "2026-06-01T12:00:00", "source": "simulator",
                                                    "IR": 100, "IY": 100, "IB": 100, "VR": 240, "VY": 240,
                                                    "VB": 240, "oil_temp": 50, "ambient_temp": 30})
            check("one sample into an empty buffer is accepted", code == 200 and r["accepted"] == 1,
                  f"HTTP {code}, {r.get('analysis_error')}")
        except urllib.error.HTTPError as e:
            check("one sample into an empty buffer is accepted", False, f"HTTP {e.code}")
        try:
            req("/api/live/sample", {"timestamp": "not a date", "source": "simulator"})
            check("a malformed sample is rejected with 422", False, "accepted")
        except urllib.error.HTTPError as e:
            check("a malformed sample is rejected with 422", e.code == 422, f"HTTP {e.code}")
        s1 = req("/api/live/status")[2]["session"]

        # stream: 15-min samples, 0.15 s apart
        log = open(LOG, "w")
        sim = subprocess.Popen([sys.executable, "-u", "-m", "dtwin.live", "--api", API, "--speed", "6000"],
                               cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        n_pre = wait_for(lambda: (lambda st: st["n_samples"] if st.get("last_sample") and st["sample_age_s"] is not None
                                  and st["n_samples"] > 200 else None)(req("/api/live/status")[2]), 60)
        print(f"    streaming before restart: {n_pre} samples in buffer")

        # restart the backend under the running simulator
        be.send_signal(signal.SIGINT)
        be.wait(10)
        time.sleep(8.0)                                   # longer than the retries: a real outage
        be = backend()

        def resumed():
            st = req("/api/live/status")[2]
            return st if st.get("n_samples", 0) >= 192 and st.get("sample_age_s") is not None \
                and st["session"] != s1 and not st["history"]["rebuilding"] else None
        st = wait_for(resumed, 60)
        time.sleep(3.0)                                   # a few live samples on the new backend
        code, hdr, st = req("/api/live/status")
        code_s, _, state = req("/api/live/state")
        sim.send_signal(signal.SIGINT)
        sim.wait(10)
        sim = None
        log.close()
        out = open(LOG).read()

        check("simulator saw zero rejected samples", "rejected" not in out,
              f"{out.count('rejected')} rejections")
        check("new session noticed and history re-sent as prewarm", "re-sent" in out and "backend restarted" in out,
              next((ln for ln in out.splitlines() if "re-sent" in ln), "no re-prewarm line"))
        check("after restart: 48 h buffer, analysis running, live samples arriving",
              st["n_samples"] >= 192 and st["analysis_error"] is None and not st["history"]["rebuilding"]
              and code_s == 200 and state.get("health") is not None and st["sample_age_s"] < 5,
              f"n={st['n_samples']} need={st['history']['need']} err={st['analysis_error']} age={st['sample_age_s']} s")
        check("session header on every response", hdr.get("x-dtwin-session") == st["session"],
              hdr.get("x-dtwin-session", "missing"))
        fail_lines = [ln for ln in out.splitlines() if "unreachable" in ln]
        check("outage reported in one line, recovery in one", len(fail_lines) == 1 and out.count("reachable again") == 1, f"{len(fail_lines)} lines")
    finally:
        if sim is not None:
            sim.kill()
        be.send_signal(signal.SIGINT)
        try:
            be.wait(10)
        except subprocess.TimeoutExpired:
            be.kill()
    left = subprocess.run(["pgrep", "-f", "dtwin.live"], capture_output=True, text=True).stdout.strip()
    left_be = subprocess.run(["pgrep", "-f", f"--port {PORT}"], capture_output=True, text=True).stdout.strip()
    check("no simulator or test backend left running", not left and not left_be, left or left_be or "clean")
    print(f"{sum(results)} of {len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())

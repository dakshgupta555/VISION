"""
Fleet simulator: every monitored transformer's physics, every node and every
existing gateway, posting what the gateways forward to the network backend.

    python -m dtwin_network.sim                       # one 15-min interval per second
    python -m dtwin_network.sim --step-s 0.5 --api http://127.0.0.1:8001

At start, and after a demo reset, 48 h of history is generated and sent as
prewarm so every twin's 24 h rolling fit is ready at once. Faults, cut links,
gateway outages and packet loss set from the dashboard are read from
/api/net/sim-config every step and change this process's physics and radio;
the backend only ever sees forwarded packets.

Stop it with Ctrl-C. After any test: pkill -f dtwin_network; pgrep -fl dtwin_network
"""

import argparse
import json
import time
import urllib.error
import urllib.request

from .fleet import load
from .radio import FleetSim

PREWARM_H = 48.0


def call(api, path, body=None, timeout=10.0):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(api + path, data=data, method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def prewarm(api, sim, hours):
    batches = {}
    for _ in range(int(round(hours * 60 / sim.interval_min))):
        for up in sim.step(prewarm=True):
            b = batches.setdefault(up["gateway_id"], {**up, "packets": []})
            b["packets"] += up["packets"]
            b["sim_time"] = up["sim_time"]
    for b in batches.values():
        call(api, "/api/net/uplink", b, timeout=60)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://127.0.0.1:8001")
    ap.add_argument("--step-s", type=float, default=1.0, help="wall seconds per reporting interval")
    ap.add_argument("--prewarm-h", type=float, default=PREWARM_H)
    args = ap.parse_args(argv)
    fleet = load()
    epoch, sim, down = None, None, False
    print(f"fleet simulator: {len(fleet.monitored)} nodes, {sum(g.existing for g in fleet.gateways.values())} gateways "
          f"-> {args.api}, one {fleet.radio['reporting_interval_min']} min interval every {args.step_s} s", flush=True)
    while True:
        t0 = time.monotonic()
        try:
            cfg = call(args.api, "/api/net/sim-config")
            if (cfg["session"], cfg["epoch"]) != epoch:
                from datetime import datetime, timedelta
                now = datetime.now().replace(second=0, microsecond=0)
                now -= timedelta(minutes=now.minute % int(fleet.radio["reporting_interval_min"]))
                sim = FleetSim(fleet, start=now - timedelta(hours=args.prewarm_h))
                prewarm(args.api, sim, args.prewarm_h)
                epoch = (cfg["session"], cfg["epoch"])
                print(f"session {epoch[0]} epoch {epoch[1]}: {args.prewarm_h:g} h history sent", flush=True)
            if not cfg.get("paused"):
                sim.configure(faults=cfg["faults"], comm_cut=cfg["comm_cut"], node_down=cfg.get("node_down", ()),
                              gateway_down=cfg["gateway_down"], packet_loss=cfg.get("packet_loss", 0.0),
                              capacity=cfg.get("capacity"))
                for up in sim.step():
                    call(args.api, "/api/net/uplink", up)
            if down:
                print("backend reachable again", flush=True)
                down = False
        except (OSError, urllib.error.URLError) as e:
            if not down:
                print(f"backend unreachable ({e}); retrying", flush=True)
                down = True
        time.sleep(max(0.0, args.step_s - (time.monotonic() - t0)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass

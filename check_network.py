"""
Network dashboard checks. Everything except the last check runs in-process
(simulator, radio and backend store wired directly, no HTTP), so it is
deterministic and leaves no process behind.

    python check_network.py              # all checks, incl. the existing dashboard's UI checks on :5173/:8000
    python check_network.py --skip-ui    # without the browser checks

Checks:
  1  independence: a fault in one DT leaves every other DT's twin state identical
  2  routing: every live packet reaches the backend through its own gateway, hop stamps in order
  3  gateways forward packets unchanged (payload at node == payload stored)
  4  capacity: a gateway never forwards more than its capacity per interval; the
     backlog waits in the nodes and drains with nothing lost
  5  stale then offline on a cut link; health unverified, never shown as verified;
     buffered packets replayed on reconnect, nothing lost
  6  missing data is never healthy: a unit with no packets has no level and is unverified
  7  malformed packets are rejected with a reason; duplicates ignored
  8  the budget is never exceeded (MILP and greedy, budgets 0 to 12 L, cost and radius scaled)
  9  the optimiser is deterministic: same input, same plan, also in a fresh process
 10  cost arithmetic: capital and recurring recomputed from the fleet file
 11  missing coordinates or costs give a clear message naming the record and field
 12  infeasible budget says so
 13  existing dashboard (:5173 / :8000) still passes its own UI checks
"""

import copy
import json
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml

from dtwin_network import planner, risk as riskmod
from dtwin_network.fleet import FLEET_YAML, FleetError, load, parse
from dtwin_network.radio import FleetSim
from dtwin_network.store import NetworkStore

ROOT = Path(__file__).resolve().parent
START = datetime(2026, 5, 1)
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)


def run(fleet, steps, before=None, prewarm=192, cfg=None, every=None):
    """Prewarm, then step `steps` intervals, delivering every uplink to a fresh
    store. before(store) sets demo state; every(k, sim, store, ups) observes."""
    sim, st = FleetSim(fleet, start=START), NetworkStore(fleet, clock=_Clock())
    for _ in range(prewarm):
        for up in sim.step(prewarm=True):
            st.uplink(up)
    if before:
        before(st)
    for k in range(steps):
        c = st.sim_config()
        sim.configure(faults=c["faults"], comm_cut=c["comm_cut"], node_down=c["node_down"],
                      gateway_down=c["gateway_down"], packet_loss=c["packet_loss"], capacity=c["capacity"])
        ups = sim.step(wall=st.wall)
        for up in ups:
            st.uplink(up)
        st.wall.tick()
        st.tick()
        if every:
            every(k, sim, st, ups)
    st.analyse_all()
    st.tick()
    return sim, st


class _Clock:
    """Deterministic wall clock: 1 s per interval, 1 ms per read."""
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        self.t += 0.001
        return self.t

    def tick(self):
        self.t += 1.0


def strip(state):
    return json.dumps({k: state[k] for k in ("status", "readings", "health", "alerts_live", "countdown")},
                      sort_keys=True, default=str)


def main():
    skip_ui = "--skip-ui" in sys.argv
    fleet = load()

    # 1 independence --------------------------------------------------------------------
    _, base = run(fleet, 24)
    _, hit = run(fleet, 24, before=lambda s: s.inject("DT-005", "overload", 1.3, 1))
    same = [d for d in base.devices if d != "DT-005" and strip(base.devices[d].state) == strip(hit.devices[d].state)]
    check("1 a fault in DT-005 leaves the other 11 twins identical", len(same) == 11,
          f"{len(same)} of 11 identical; DT-005 {base.devices['DT-005'].level} -> {hit.devices['DT-005'].level}")
    check("1 ... and DT-005 itself goes to alarm", hit.devices["DT-005"].level == "alarm"
          and base.devices["DT-005"].level == "ok")
    grp = [a.id for a in fleet.monitored if a.cohort == fleet.assets["DT-005"].cohort and a.id != "DT-005"]
    check("1 ... including units of the same manufacturer and rating", all(g in same for g in grp), ", ".join(grp) or "none")

    # 2, 3 routing and unchanged forwarding --------------------------------------------
    seen = {"bad_route": 0, "bad_order": 0, "n": 0, "changed": 0}

    def watch(k, sim, st, ups):
        for up in ups:
            for p in up["packets"]:
                seen["n"] += 1
                if fleet.assets[p["device_id"]].gateway != up["gateway_id"] or p["gateway_id"] != up["gateway_id"]:
                    seen["bad_route"] += 1
                if not (p["tx_wall"] <= p["gw_wall"]):
                    seen["bad_order"] += 1
                d = st.devices[p["device_id"]]
                import pandas as pd
                row = d.rows.get(pd.Timestamp(p["ts"]))
                if row is None or any((v is None and row[c] == row[c]) or (v is not None and row[c] != v)
                                      for c, v in p["payload"].items()):
                    seen["changed"] += 1
    _, st = run(fleet, 12, every=watch)
    chains = [e["chain"] for e in st.events if e.get("chain")]
    lp_ok = all(st.devices[d].last_packet["chain"][1]["who"] == fleet.assets[d].gateway for d in st.devices)
    order_ok = all(h0["wall"] <= h1["wall"] <= h2["wall"] for h0, h1, h2 in
                   (st.devices[d].last_packet["chain"] for d in st.devices))
    check("2 every packet travelled node -> its own gateway -> backend", seen["bad_route"] == 0 and lp_ok and seen["n"] == 144,
          f"{seen['n']} packets, {seen['bad_route']} misrouted")
    check("2 hop stamps in order: node <= gateway <= backend", seen["bad_order"] == 0 and order_ok)
    fw = {g["id"]: g["forwarded"] for g in st.gateways_json()}
    per = {g: sum(st.devices[a.id].received for a in fleet.monitored if a.gateway == g) for g in fw}
    check("2 gateway forwarded counts equal its nodes' received counts", fw == per, str(fw))
    check("3 gateways forward packets unchanged (payload at node == payload stored)", seen["changed"] == 0,
          f"{seen['changed']} altered")

    # 4 capacity --------------------------------------------------------------------------
    worst = {"max": 0}
    cap = 2

    def capwatch(k, sim, st, ups):
        for up in ups:
            if up["gateway_id"] == "GW-A" and k <= 7:             # capped for intervals 0-7
                worst["max"] = max(worst["max"], len(up["packets"]))
        if k == 7:                                   # lift the limit; the backlog must drain
            st.demo["capacity"] = {}

    _, st = run(fleet, 20, before=lambda s: s.demo.__setitem__("capacity", {"GW-A": cap}), every=capwatch)
    gw_a = [a.id for a in fleet.monitored if a.gateway == "GW-A"]
    drained = all(st.devices[d].received == st.devices[d].last_seq for d in gw_a)
    check(f"4 GW-A at capacity {cap}: never more than {cap} packets per interval", worst["max"] <= cap, f"max {worst['max']}")
    check("4 backlog waits in the nodes and drains once capacity returns, nothing lost",
          drained and all(st.devices[d].lost == 0 for d in gw_a) and all(st.devices[d].last_seq == 192 + 20 for d in gw_a),
          f"received {[st.devices[d].received for d in gw_a]}")

    # 5 stale / offline / replay ---------------------------------------------------------
    trace = []
    cut = "DT-004"

    def linkwatch(k, sim, st, ups):
        trace.append((k, st.comms(st.devices[cut])["status"], st.summary(cut)["health"]["verified"]))
        if k == 9:
            st.set_link(cut, False)

    _, st = run(fleet, 14, before=lambda s: s.set_link(cut, True), every=linkwatch)
    states = [t[1] for t in trace]
    first_stale = states.index("stale") if "stale" in states else None
    first_off = states.index("offline") if "offline" in states else None
    check("5 cut link: online -> stale after 2 missed intervals -> offline after 6",
          first_stale == 1 and first_off == 5, f"sequence {states[:10]}")
    check("5 health unverified whenever not online; status words only online/stale/offline",
          all(not v for k, s, v in trace if s != "online") and set(states) <= {"online", "stale", "offline"})
    d = st.devices[cut]
    check("5 on reconnect the buffered packets are replayed, nothing lost, back online",
          d.received == d.last_seq == 192 + 14 and d.lost == 0 and d.late >= 9 and st.comms(d)["status"] == "online",
          f"received {d.received} of {d.last_seq}, late {d.late}")
    others = [x for x in st.devices if x != cut and st.comms(st.devices[x])["status"] != "online"]
    check("5 the other 11 stayed online", not others, ", ".join(others))

    # 6 missing data is never healthy ----------------------------------------------------
    empty = NetworkStore(fleet)
    s0 = empty.summary("DT-001")
    check("6 a unit with no packets: no health level, unverified, comms 'no data', confidence none",
          s0["health"]["level"] is None and not s0["health"]["verified"] and s0["comms"]["status"] == "no data"
          and s0["confidence"]["level"] == "none")
    r0 = riskmod.monitored(fleet.assets["DT-001"], s0)
    check("6 ... and its risk is not 'normal' (communication term drives it)", r0["likelihood"] > 0, r0["driver"])

    # 7 malformed packets ------------------------------------------------------------------
    st = NetworkStore(fleet)
    good = {"device_id": "DT-001", "seq": 1, "ts": "2026-05-01T00:15:00",
            "payload": {c: 1.0 for c in ("IR", "IY", "IB", "VR", "VY", "VB", "oil_temp", "ambient_temp")}}
    bad = [dict(good, seq=0), dict(good, device_id="DT-999"), dict(good, ts="yesterday"), dict(good, payload="x"),
           dict(good, seq=2, payload={**good["payload"], "IR": "abc"})]
    r = st.uplink({"gateway_id": "GW-A", "sim_time": "2026-05-01T00:15:00", "packets": [good, good] + bad})
    check("7 five malformed packets rejected with reasons, one duplicate ignored, one stored",
          r["accepted"] == 2 and r["rejected"] == 5 and st.devices["DT-001"].received == 1
          and st.devices["DT-001"].duplicates == 1, f"{r}")
    try:
        st.uplink({"gateway_id": "GW-Z", "sim_time": "2026-05-01T00:15:00", "packets": []})
        ok = False
    except ValueError as e:
        ok = "unknown gateway" in str(e)
    check("7 an uplink from an unknown gateway is refused", ok)

    # 8 budget never exceeded ----------------------------------------------------------------
    over, n = [], 0
    for B in range(0, 1200001, 25000):
        for sc in ({}, {"node_scale": 1.2}, {"gw_scale": 0.8}, {"radius_scale": 1.2}):
            for p in (planner.solve(fleet, B, **sc), planner.greedy(fleet, B, **sc)):
                n += 1
                if not p["feasible"] or p["capital_inr"] > B + 0.5 or not p["within_budget"]:
                    over.append((B, sc, p["kind"], p.get("capital_inr")))
    check("8 capital never above the budget: MILP and greedy, 49 budgets x 4 cost/radius settings",
          not over, f"{n} plans, {len(over)} over")
    caps = []
    for B in (300000, 600000, 1200000):
        p = planner.solve(fleet, B)
        caps += [g for g in p["gateway_load"] if g["used"] > g["capacity"]]
        caps += [a for a, g in p["assign"].items() if not fleet.in_range(fleet.assets[a], fleet.gateways[g])]
    check("8 ... and never above a gateway's capacity or beyond its coverage radius", not caps, str(caps[:3]))
    p = planner.solve(fleet, 600000)
    g = planner.greedy(fleet, 600000)
    check("8 the optimised plan covers at least as much risk as greedy at the same budget",
          all(planner.solve(fleet, B)["risk_covered"] >= planner.greedy(fleet, B)["risk_covered"] - 1e-6
              for B in range(0, 1200001, 50000)), f"at 6 L: {p['risk_covered']} vs {g['risk_covered']}")

    # 9 deterministic ------------------------------------------------------------------------------
    keys = ("nodes", "assign", "gateways_opened", "capital_inr", "annual_inr", "risk_covered")
    a1 = {k: planner.solve(fleet, 550000)[k] for k in keys}
    a2 = {k: planner.solve(fleet, 550000)[k] for k in keys}
    code = ("import json; from dtwin_network.fleet import load; from dtwin_network import planner; "
            f"p = planner.solve(load(), 550000); print(json.dumps({{k: p[k] for k in {keys!r}}}, sort_keys=True))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    check("9 same input, same plan: twice in-process and once in a fresh process",
          a1 == a2 and out.returncode == 0 and json.loads(out.stdout) == json.loads(json.dumps(a1, sort_keys=True)),
          f"{len(a1['nodes'])} nodes, {a1['gateways_opened']}")

    # 10 cost arithmetic --------------------------------------------------------------------------
    bad = []
    for B in (0, 250000, 550000, 1000000):
        for p in (planner.solve(fleet, B), planner.greedy(fleet, B)):
            cap_ = sum(fleet.assets[a].node_cost_inr for a in p["nodes"]) + sum(fleet.gateways[gg].cost_inr for gg in p["gateways_opened"])
            ann = sum(fleet.assets[a].node_annual_inr for a in p["nodes"]) + sum(fleet.gateways[gg].annual_inr for gg in p["gateways_opened"])
            rc = round(sum(riskmod.unmonitored(fleet.assets[a])["risk"] for a in p["nodes"]), 1)
            if p["capital_inr"] != round(cap_) or p["annual_inr"] != round(ann) or abs(p["risk_covered"] - rc) > 0.05:
                bad.append((B, p["kind"]))
    sc = planner.solve(fleet, 550000, node_scale=1.2, gw_scale=0.8)
    sc_cost = sum(fleet.assets[a].node_cost_inr * 1.2 for a in sc["nodes"]) + sum(fleet.gateways[gg].cost_inr * 0.8 for gg in sc["gateways_opened"])
    check("10 capital, recurring and risk covered recomputed from the fleet file match",
          not bad and sc["capital_inr"] == round(sc_cost), f"{bad}")
    check("10 rupee grouping is Indian: 5,48,000", planner.rupees(548000) == "₹5,48,000" and planner.rupees(12345678) == "₹1,23,45,678")

    # 11 clear messages ------------------------------------------------------------------------
    raw = yaml.safe_load(open(FLEET_YAML))
    msgs = []
    for mutate, must in (
            (lambda c: c["assets"][20].pop("xy_km"), [raw["assets"][20]["id"], "xy_km"]),
            (lambda c: (c["assets"][3].pop("node_cost_inr"), c["costs"].pop("node_cost_inr")), [raw["assets"][3]["id"], "node_cost_inr"]),
            (lambda c: (c["gateways"][5].pop("cost_inr"), c["costs"].pop("gateway_cost_inr")), [raw["gateways"][5]["id"], "cost_inr"]),
            (lambda c: c["assets"][0].__setitem__("xy_km", ["a", 1]), [raw["assets"][0]["id"], "xy_km"])):
        c = copy.deepcopy(raw)
        mutate(c)
        try:
            parse(c)
            msgs.append(None)
        except FleetError as e:
            msgs.append(str(e) if all(m in str(e) for m in must) else None)
    check("11 missing coordinates or costs: refused, naming the record and the field", all(msgs),
          msgs[0] if msgs[0] else str(msgs))

    # 12 infeasible ----------------------------------------------------------------------------------
    p = planner.solve(fleet, 10000)
    check("12 a budget below the cheapest addition says so", p["nodes"] == [] and "can be added within this budget" in p.get("message", ""),
          p.get("message"))

    # 13 existing dashboard --------------------------------------------------------------------------
    if skip_ui:
        print("  SKIP  13 existing dashboard UI checks (--skip-ui)")
    else:
        try:
            urllib.request.urlopen("http://127.0.0.1:8000/api/scenarios", timeout=5).read()
            urllib.request.urlopen("http://127.0.0.1:5173", timeout=5).read()
            up = True
        except OSError as e:
            up, why = False, str(e)
        if not up:
            check("13 existing dashboard on :5173/:8000 reachable", False, f"start both first: {why}")
        else:
            r = subprocess.run(["node", "scripts/ui_checks.mjs", "--out", str(ROOT / "screens")], cwd=ROOT / "dashboard",
                               capture_output=True, text=True, timeout=600)
            lines = [ln for ln in r.stdout.splitlines() if ln.startswith(("PASS", "FAIL"))]
            npass = sum(ln.startswith("PASS") for ln in lines)
            check("13 existing dashboard (:5173/:8000) passes its own UI checks", r.returncode == 0 and lines and npass == len(lines),
                  f"{npass}/{len(lines)}" + ("" if r.returncode == 0 else f"; {(r.stderr or r.stdout)[-300:]}"))

    n_ok = sum(results)
    print(f"\n{n_ok} of {len(results)} network checks passed")
    sys.exit(0 if n_ok == len(results) else 1)


if __name__ == "__main__":
    main()

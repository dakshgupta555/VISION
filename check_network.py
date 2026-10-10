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


def geo_checks(fleet, skip_ui):
    """14-22: geography, coverage, the facility-location MILP, the demo, the map UI."""
    from dtwin_network import geo
    from dtwin_network.demo import Demo

    # 14 haversine against reference distances (WGS84 geodesic references; sphere within 0.5 %)
    refs = [("1 degree of latitude on the mean sphere", (0, 77.0, 1, 77.0), 111_195.1),
            ("London - Paris", (51.5074, -0.1278, 48.8566, 2.3522), 343_920.0),
            ("Los Angeles LAX - New York JFK", (33.9425, -118.4081, 40.6398, -73.7789), 3_983_000.0)]
    errs = [(n, abs(geo.haversine_m(*p) - ref) / ref) for n, p, ref in refs]
    check("14 haversine within 0.5 % of reference distances", all(e < 0.005 for _, e in errs),
          ", ".join(f"{n.split(' ')[0]} {100 * e:.2f} %" for n, e in errs))
    m = planner.Model(fleet)
    full = all(abs(m.d[a.id][g.id] - geo.haversine_m(a.lat, a.lon, g.lat, g.lon)) < 1e-6 for a in m.T for g in m.G)
    check("14 distance matrix = haversine for all 52 x 8 transformer-gateway pairs", full and len(m.d) == 52)

    # 15 coverage: inside / outside the radius, and a radius edit moves feasibility as expected
    a, g = fleet.assets["DT-001"], fleet.gateways["GW-A"]
    d = fleet.distance_m(a, g)
    ins = geo.pair(a, g, d, d + 1)["feasible"] and not geo.pair(a, g, d, d - 1)["feasible"]
    check("15 a point just inside the radius is covered, just outside is not", ins, f"DT-001 to GW-A {d:.0f} m")
    cut = planner.Model(fleet, overrides={"GW-A": {"radius_m": 1500}})
    expect = sorted(x.id for x in fleet.assets.values()
                    if m.ok[x.id, "GW-A"] and geo.haversine_m(x.lat, x.lon, g.lat, g.lon) > 1500)
    moved = sorted(x.id for x in fleet.assets.values() if m.ok[x.id, "GW-A"] and not cut.ok[x.id, "GW-A"])
    check("15 cutting GW-A's radius to 1.5 km removes exactly the transformers beyond 1.5 km", moved == expect and moved,
          f"{len(moved)} lost, e.g. {', '.join(moved[:4])}")
    lbm = planner.Model(fleet, link_budget=True)
    weak = [(x.id, gg.id) for x in m.T for gg in m.G if m.ok[x.id, gg.id] and not lbm.ok[x.id, gg.id]]
    wp = geo.pair(fleet.assets[weak[0][0]], fleet.gateways[weak[0][1]], m.d[weak[0][0]][weak[0][1]],
                  fleet.gateways[weak[0][1]].radius_m, fleet.link_budget, True) if weak else None
    check("15 link budget on: some in-radius pairs fail on estimated margin, with that reason", bool(weak)
          and "margin" in wp["why"] and wp["within_assumed_radius"], f"{len(weak)} pairs, e.g. {weak[0] if weak else ''}")

    # 16 capacity and feasibility of every assignment, many assumption sets
    bad, n = [], 0
    for kw in ({}, {"link_budget": True}, {"overrides": {"GW-A": {"radius_m": 1500}, "CG-2": {"cost_inr": 40000}}},
               {"radius_scale": 0.8}, {"overrides": {"GW-B": {"max_nodes": 4}}}):
        mm = planner.Model(fleet, **{k: v for k, v in kw.items() if k in ("overrides", "link_budget", "radius_scale")})
        for B in (0, 300000, 700000, 1200000):
            for p in (planner.solve(fleet, B, model=mm), planner.greedy(fleet, B, model=mm)):
                n += 1
                load = {}
                for t, gg in p["assign"].items():
                    load[gg] = load.get(gg, 0) + 1
                    if not mm.ok[t, gg] or gg not in p["gateways_built"]:
                        bad.append((t, gg, p["kind"]))
                bad += [(gg, "over capacity") for gg, k in load.items() if k > mm.cap[gg]]
    check("16 no assignment to an infeasible or unbuilt gateway, no gateway over max_nodes", not bad,
          f"{n} plans, {len(bad)} violations {bad[:2]}")

    # 17 budget sweep: 25 budgets, never exceeded, optimiser >= greedy
    over, worse = [], []
    for B in range(0, 1200001, 50000):
        for kw in ({}, {"link_budget": True}):
            o, gr = planner.solve(fleet, B, **kw), planner.greedy(fleet, B, **kw)
            if o["capital_inr"] > B or gr["capital_inr"] > B or not o["within_budget"]:
                over.append(B)
            if o["risk_covered"] < gr["risk_covered"] - 1e-6:
                worse.append(B)
    check("17 25 budgets x link budget off/on: never over budget, optimiser risk >= greedy at every budget",
          not over and not worse, f"over {over[:3]}, worse {worse[:3]}")

    # 18 each high-risk transformer left out carries the correct binding reason (brute force)
    wrong, seen = [], set()
    for kw, B in (({}, 250000), ({"radius_scale": 0.2}, 1200000),
                  ({"radius_scale": 0.8, "overrides": {"GW-A": {"max_nodes": 5}}}, 1200000), ({"link_budget": True}, 400000)):
        mm = planner.Model(fleet, **kw)
        p = planner.solve(fleet, B, model=mm)
        load = {x["id"]: x["used"] for x in p["gateway_load"]}
        for u in p["uncovered_high"]:
            seen.add(u["reason"])
            feas = [gg.id for gg in mm.G if geo.haversine_m(fleet.assets[u["id"]].lat, fleet.assets[u["id"]].lon,
                                                             gg.lat, gg.lon) <= mm.radius[gg.id] and mm.ok[u["id"], gg.id]]
            built = [x for x in feas if x in p["gateways_built"]]
            truth = ("range" if not feas else "capacity" if built and all(load[x] >= mm.cap[x] for x in built)
                     and not [x for x in feas if x not in p["gateways_built"]] else "budget")
            if truth != u["reason"]:
                wrong.append((u["id"], u["reason"], truth))
    check("18 high-risk transformers left out: reason (range / capacity / budget) matches brute force",
          not wrong and seen == {"range", "capacity", "budget"}, f"reasons seen {sorted(seen)}, wrong {wrong[:2]}")

    # 19 uncovered detection matches a brute-force check
    mism = 0
    for kw, B in (({}, 0), ({}, 550000), ({"link_budget": True}, 400000), ({"overrides": {"GW-C": {"radius_m": 800}}}, 300000)):
        mm = planner.Model(fleet, **kw)
        p = planner.solve(fleet, B, model=mm)
        lb_on = mm.use_lb
        brute = sorted(t.id for t in fleet.assets.values() if not any(
            geo.haversine_m(t.lat, t.lon, fleet.gateways[gg].lat, fleet.gateways[gg].lon) <= mm.radius[gg]
            and (not lb_on or geo.link_margin_db(geo.haversine_m(t.lat, t.lon, fleet.gateways[gg].lat, fleet.gateways[gg].lon),
                                                 fleet.link_budget) >= fleet.link_budget["required_margin_db"])
            for gg in p["gateways_built"]))
        mism += brute != p["uncovered"]
    check("19 uncovered transformers = brute-force check, 4 assumption sets", mism == 0)

    # 20-21 demo end to end, deterministic
    d1, d2 = Demo(fleet), Demo(fleet)
    steps = [d1.step(k) for k in range(1, 7)]
    fails = [(s["step"], k) for s in steps for k, ok in s["expect"].items() if not ok]
    check("20 six-step demo: every step's expected state holds", not fails, f"{fails[:3]}")
    s2, s3 = steps[1]["overview"]["devices"], steps[2]["overview"]["devices"]
    o2 = next(x for x in s2 if x["id"] == "DT-004")
    o3 = next(x for x in s3 if x["id"] == "DT-004")
    check("20 comm outage: DT-004 stale, health unchanged from before the cut, no fault alarm",
          o3["comms"]["status"] == "stale" and o3["health"]["index"] == o2["health"]["index"]
          and o3["health"]["level"] == "ok" and not o3["twin"]["alerts"] and not o3["health"]["verified"],
          f"index {o2['health']['index']} -> {o3['health']['index']}, {o3['comms']['status']}")
    one = {x["id"]: x["health"] for x in steps[0]["overview"]["devices"]}
    two = {x["id"]: x["health"] for x in s2}
    changed = sorted(k for k in one if one[k]["level"] != two[k]["level"])
    check("20 abnormal event: only DT-007's state changes between steps 1 and 2", changed == ["DT-007"], ", ".join(changed))
    same = all(json.dumps({k: v for k, v in d2.step(s["step"]).items() if k != "overview"}, sort_keys=True, default=str)
               == json.dumps({k: v for k, v in s.items() if k != "overview"}, sort_keys=True, default=str) for s in steps)
    check("21 demo is deterministic: a second run gives identical captions, plans and highlights", same)
    d1.reset()
    check("21 reset returns to step 1", d1.done == 0 and d1.step(1)["caption"] == steps[0]["caption"])

    # 22 map UI: each demo step and the offline fallback at 1366x768, no console errors
    if skip_ui:
        print("  SKIP  22 map UI screenshots (--skip-ui)")
        return
    url = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--net-url=")), "http://localhost:5180")
    try:
        urllib.request.urlopen(url, timeout=5).read()
        urllib.request.urlopen(url + "/api/net/geo", timeout=10).read()
    except OSError as e:
        check("22 network app reachable for the map UI checks", False, f"{url}: {e}")
        return
    outs = []
    for k in range(1, 7):
        r = subprocess.run(["node", "scripts/shot.mjs", "--url", url, "--tab", "map", "--demo", str(k), "--full",
                            "--out", str(ROOT / "screens" / f"geo_demo_{k}.png"), "--wait", "2500"],
                           cwd=ROOT / "network-dashboard", capture_output=True, text=True, timeout=300)
        outs.append((k, r.returncode, r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-200:]))
    r = subprocess.run(["node", "scripts/shot.mjs", "--url", url, "--tab", "map", "--offline", "--full",
                        "--out", str(ROOT / "screens" / "geo_offline.png"), "--wait", "8000"],
                       cwd=ROOT / "network-dashboard", capture_output=True, text=True, timeout=300)
    off = r.returncode == 0 and "map offline=yes" in r.stdout
    check("22 map: six demo-step screenshots at 1366x768, no console errors", all(rc == 0 for _, rc, _ in outs),
          "; ".join(f"{k}:{'ok' if rc == 0 else msg}" for k, rc, msg in outs))
    check("22 map with tiles blocked switches to the offline grid, no console errors", off,
          (r.stdout.strip().splitlines() or [""])[0][-120:])


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
            (lambda c: c["assets"][20].pop("lat"), [raw["assets"][20]["id"], "lat"]),
            (lambda c: (c["assets"][3].pop("node_cost_inr"), c["costs"].pop("node_cost_inr")), [raw["assets"][3]["id"], "node_cost_inr"]),
            (lambda c: (c["gateways"][5].pop("install_cost_inr"), c["costs"].pop("gateway_cost_inr")), [raw["gateways"][5]["id"], "install_cost_inr"]),
            (lambda c: c["assets"][0].__setitem__("lon", "east"), [raw["assets"][0]["id"], "lon"])):
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

    geo_checks(fleet, skip_ui)

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

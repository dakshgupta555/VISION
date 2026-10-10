"""
Repeatable demo: six steps, seeded and deterministic, run in-process on its own
simulator and backend store (never the live :8001 stream), so every run gives
the same states. Back/Next only move between cached steps; reset returns to 1.

    python -m dtwin_network.demo          # walks the six steps, prints each caption, checks it

  1  Initial network with estimated coverage
  2  Abnormal event on ONE transformer (fault injected into its own twin's physics)
  3  A separate comm outage on another transformer: stale, not a fault
  4  An uncovered high-risk transformer is highlighted
  5  The optimiser recommends a gateway that covers it
  6  A different budget gives a different feasible plan

Captions are fixed templates filled with the step's own numbers.
"""

import sys
from datetime import datetime

from . import planner, risk as riskmod, views
from .fleet import load
from .radio import FleetSim
from .store import NetworkStore

START = datetime(2026, 5, 1)
PREWARM = 192
FAULT_DT, FAULT = "DT-007", {"kind": "overload", "severity": 1.3, "ramp_h": 1.0}
OUTAGE_DT = "DT-004"
BUDGET_A, BUDGET_B = 350000, 550000
STEP_INTERVALS = {1: 4, 2: 6, 3: 3, 4: 0, 5: 0, 6: 0}
TITLES = {1: "Initial network", 2: "Abnormal event on one transformer", 3: "Communication outage, not a fault",
          4: "Uncovered high-risk transformer", 5: "Optimiser recommends a gateway", 6: "A different budget"}


class _Clock:
    """Deterministic wall clock: starts at a fixed instant, 1 s per interval."""
    def __init__(self):
        self.t = START.timestamp() + 10 * 3600

    def __call__(self):
        self.t += 0.001
        return self.t

    def tick(self):
        self.t += 1.0


class Demo:
    def __init__(self, fleet=None):
        self.fleet = fleet or load()
        self.reset()

    def reset(self):
        self.sim = FleetSim(self.fleet, start=START)
        self.store = NetworkStore(self.fleet, clock=_Clock())
        self.cache = {}
        self.done = 0
        for _ in range(PREWARM):
            for up in self.sim.step(prewarm=True):
                self.store.uplink(up)

    def _run(self, n):
        st, sim = self.store, self.sim
        for _ in range(n):
            c = st.sim_config()
            sim.configure(faults=c["faults"], comm_cut=c["comm_cut"], node_down=c["node_down"],
                          gateway_down=c["gateway_down"], packet_loss=c["packet_loss"], capacity=c["capacity"])
            for up in sim.step(wall=st.wall):
                st.uplink(up)
            st.wall.tick()
            st.analyse_all()
            st.tick()

    def step(self, k):
        k = int(k)
        if not 1 <= k <= 6:
            raise ValueError("demo step must be 1 to 6")
        while self.done < k:
            nxt = self.done + 1
            if nxt == 2:
                self.store.inject(FAULT_DT, FAULT["kind"], FAULT["severity"], FAULT["ramp_h"])
            if nxt == 3:
                self.store.set_link(OUTAGE_DT, True)
            self._run(STEP_INTERVALS[nxt])
            self.cache[nxt] = self._snapshot(nxt)
            self.done = nxt
        return self.cache[k]

    # ------------------------------------------------------------------ one step
    def _snapshot(self, k):
        st, fl = self.store, self.fleet
        ov = views.overview(st)
        risks = riskmod.static_risks(fl)
        budget = BUDGET_B if k == 6 else BUDGET_A
        b = planner.bundle(fl, budget, risks=risks)
        cur = b["current"]
        target = next((aid for aid, r in sorted(risks.items(), key=lambda kv: (-kv[1]["risk"], kv[0]))
                       if r["band"] == "high" and aid in cur["uncovered"]), None)
        devs = {d["id"]: d for d in ov["devices"]}
        f, o = devs[FAULT_DT], devs[OUTAGE_DT]
        opt = b["optimised"]
        show_plan = k >= 5
        highlight = {"assets": [], "gateways": []}
        if k == 1:
            cap = (f"{len(devs)} monitored transformers on {len(cur['gateways_built'])} existing gateways. "
                   f"Estimated proximity coverage reaches {cur['n_within_coverage']} of {cur['n_transformers']} "
                   f"transformers ({cur['pct_within_coverage']:.0f} %); {len(cur['uncovered'])} are outside every "
                   "existing gateway's assumed radius.")
        elif k == 2:
            n_ok = sum(1 for d in devs.values() if d["id"] != FAULT_DT and d["health"]["level"] == "ok")
            cap = (f"{FAULT['kind'].capitalize()} injected into {FAULT_DT}'s own physics. Its twin now reports "
                   f"'{f['health']['label']}' (health {f['health']['index']:.0f}). The other {n_ok} transformers are "
                   "unchanged: each twin reads only its own packets.")
            highlight["assets"] = [FAULT_DT]
        elif k == 3:
            cap = (f"{OUTAGE_DT}'s radio link is cut. Communication: {o['comms']['status'].upper()} "
                   f"({o['comms']['missed']} intervals missed). Its health is last known ({o['health']['index']:.0f}), "
                   "unverified, and no fault alarm is raised: lost communication is not a transformer fault.")
            highlight["assets"] = [OUTAGE_DT]
        elif k == 4:
            dmin = min(b["pairs"][target], key=lambda p: p["distance_m"]) if target else None
            cap = (f"{target} is high risk (risk {risks[target]['risk']:.0f}: {risks[target]['driver'].split('; ')[-1]}) "
                   f"and outside every existing gateway: the nearest site, {dmin['gateway']}, is "
                   f"{dmin['distance_m'] / 1000:.2f} km away" + (" but is only a candidate." if not dmin["existing"] else ".")
                   ) if target else "Every high-risk transformer is inside existing coverage."
            highlight["assets"] = [target] if target else []
        else:
            gw = opt["assign"].get(target) if target else None
            first = next((r["text"] for r in opt["reasons"] if r["kind"] == "gateway" and r["id"] == gw), None)
            cap = (f"Budget {planner.rupees(budget)}: the optimiser builds {', '.join(opt['gateways_opened']) or 'no new gateway'} "
                   f"and instruments {len(opt['nodes'])} transformers for {planner.rupees(opt['capital_inr'])}; "
                   f"{opt['high_covered']} of {opt['high_total']} high-risk units covered. "
                   + (first or "") + (f" Greedy covers {b['greedy']['high_covered']} for {planner.rupees(b['greedy']['capital_inr'])}."))
            highlight["assets"] = [target] if target else []
            highlight["gateways"] = opt["gateways_opened"]
        return {"step": k, "n_steps": 6, "title": TITLES[k], "caption": cap, "budget": budget,
                "overview": ov, "plan": b, "show_plan": show_plan, "highlight": highlight, "target": target,
                "expect": self._expect(k, devs, b, target)}

    def _expect(self, k, devs, b, target):
        """What a correct step looks like; checked by the CLI and check_network."""
        opt = b["optimised"]
        e = {}
        touched = ({FAULT_DT} if k >= 2 else set()) | ({OUTAGE_DT} if k >= 3 else set())
        e["every other transformer normal and online"] = all(
            d["health"]["level"] == "ok" and d["comms"]["status"] == "online" and d["health"]["verified"]
            for i, d in devs.items() if i not in touched)
        if k == 1:
            e["all 12 normal, >= 10 monitored, >= 2 gateways"] = (all(d["health"]["level"] == "ok" for d in devs.values())
                                                                 and len(devs) >= 10 and len(b["current"]["gateways_built"]) >= 2)
        if k >= 2:
            e[f"{FAULT_DT} in alarm"] = devs[FAULT_DT]["health"]["level"] == "alarm"
        if k >= 3:
            o = devs[OUTAGE_DT]
            e[f"{OUTAGE_DT} stale, unverified, no fault"] = (o["comms"]["status"] == "stale" and not o["health"]["verified"]
                                                            and o["health"]["level"] == "ok"
                                                            and not (o.get("twin") or {}).get("alerts"))
        if k >= 4:
            e["a high-risk transformer outside existing coverage"] = target is not None
        if k == 5:
            e[f"plan covers {target} through a new gateway"] = (target in opt["nodes"]
                                                                and opt["assign"].get(target) in opt["gateways_opened"])
        if k == 6:
            p5 = self.cache.get(5)
            e["different feasible plan within budget"] = (opt["feasible"] and opt["within_budget"] and p5 is not None
                                                          and (opt["nodes"], opt["gateways_opened"])
                                                          != (p5["plan"]["optimised"]["nodes"], p5["plan"]["optimised"]["gateways_opened"]))
        return e


def main():
    d = Demo()
    bad = 0
    for k in range(1, 7):
        s = d.step(k)
        print(f"\n[{k}/6] {s['title']}\n  {s['caption']}")
        for name, ok in s["expect"].items():
            print(f"  {'PASS' if ok else 'FAIL'}  {name}")
            bad += not ok
    print(f"\n{'all steps as expected' if not bad else f'{bad} expectation(s) failed'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

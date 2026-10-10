"""
Deployment planner: a capacitated facility-location / maximum-coverage MILP.
Which unmonitored transformers get a node, which candidate gateway sites are
built, and which gateway every node reports through, for a capital budget.

  sets        T transformers (installed nodes and candidates), G gateways
              (existing, always built, and candidate sites), F = feasible pairs:
              (t, g) with distance <= g's assumed radius AND, when the link
              budget is on, estimated margin >= required margin (geo.py)
  variables   z_t in {0,1}   transformer t has a node (installed: fixed 1)
              y_g in {0,1}   gateway g built (existing: fixed 1)
              x_tg in {0,1}  t reports through g, only for (t, g) in F
  maximise    sum_t r_t z_t (new nodes only)
              - eps_c * capital cost             (equal risk: prefer cheaper)
              - eps_m * installed nodes moved off their current gateway
  subject to  sum_g x_tg = z_t                  one feasible gateway per node
              x_tg <= y_g                       only through a built gateway
              sum_t x_tg <= max_nodes_g         capacity, all nodes counted
              sum_t c_t z_t (new) + sum_g C_g y_g (candidates) <= B     never exceeded

Both tie-breakers together are below 0.03 risk points, so they never trade
risk away. An installed node with no feasible gateway under the assumptions
being tested (a radius cut too far) cannot be placed: it is reported as
"would lose coverage", never silently dropped.

Greedy baseline: from the current deployment, repeatedly add the transformer
with the highest risk per rupee of what adding it costs now (its node, plus the
cheapest buildable gateway that can take it if no built one has room).

The risk score for unmonitored transformers uses metadata only
(dtwin_network/risk.py), so the same input always gives the same plan.
"""

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from . import geo, risk as riskmod

EPS_COST = 1e-9          # per rupee
EPS_MOVE = 1e-3          # per installed node moved to another gateway
KEEP = 1000.0            # reward for keeping an installed node connected: above the most one
                         # freed slot could gain (one node, risk <= 100), small enough that the
                         # solver's optimality gap stays far below a risk point
MIP_OPTIONS = {"disp": False, "presolve": True, "mip_rel_gap": 1e-9}


def rupees(v):
    """₹ with Indian digit grouping: 1,42,000."""
    v = int(round(float(v)))
    s = str(abs(v))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:]); head = head[:-2]
        s = ",".join(([head] if head else []) + groups + [tail])
    return f"{'-' if v < 0 else ''}₹{s}"


# --------------------------------------------------------------------- setup
class Model:
    """Everything a plan depends on, after overrides and scales: one place that
    decides feasibility, so the MILP, greedy, the reasons and the UI agree."""

    def __init__(self, fleet, node_scale=1.0, gw_scale=1.0, radius_scale=1.0, overrides=None,
                 link_budget=None):
        self.fleet = fleet
        ov = overrides or {}
        self.T = sorted(fleet.assets.values(), key=lambda a: (not a.monitored, a.id))
        self.G = sorted(fleet.gateways.values(), key=lambda g: (not g.existing, g.id))
        self.radius = {g.id: float(ov.get(g.id, {}).get("radius_m", g.radius_m)) * radius_scale for g in self.G}
        self.gcost = {g.id: (0.0 if g.existing else float(ov.get(g.id, {}).get("cost_inr", g.cost_inr)) * gw_scale)
                      for g in self.G}
        self.install_cost = {g.id: float(ov.get(g.id, {}).get("cost_inr", g.cost_inr)) * gw_scale for g in self.G}
        self.cap = {g.id: int(ov.get(g.id, {}).get("max_nodes", g.capacity)) for g in self.G}
        self.ncost = {a.id: (0.0 if a.monitored else a.node_cost_inr * node_scale) for a in self.T}
        lb = dict(fleet.link_budget or {})
        self.use_lb = bool(lb.get("enabled")) if link_budget is None else bool(link_budget)
        self.lb = lb if lb.get("sensitivity_dbm") else None
        if self.use_lb and not self.lb:
            raise ValueError("link budget requested but config_network/fleet.yaml has no link_budget block")
        self.d = {a.id: {g.id: fleet.distance_m(a, g) for g in self.G} for a in self.T}
        self.ok = {}
        for a in self.T:
            for g in self.G:
                p = geo.pair(a, g, self.d[a.id][g.id], self.radius[g.id], self.lb, self.use_lb)
                self.ok[a.id, g.id] = p["feasible"]
        self.scales = {"node_cost_scale": node_scale, "gateway_cost_scale": gw_scale, "radius_scale": radius_scale}
        self.overrides = ov

    def pairs_for(self, aid, room=None):
        """Every gateway for one transformer, nearest first, with the separate
        fields and feasibility (room: gateway id -> has capacity)."""
        a = self.fleet.assets[aid]
        out = []
        for g in sorted(self.G, key=lambda g: (self.d[aid][g.id], g.id)):
            p = geo.pair(a, g, self.d[aid][g.id], self.radius[g.id], self.lb, self.use_lb,
                         room=True if room is None else room.get(g.id, True))
            p.update(existing=g.existing, assumed_radius_m=round(self.radius[g.id]))
            out.append(p)
        return out


def solve(fleet, budget, node_scale=1.0, gw_scale=1.0, radius_scale=1.0, risks=None, force=None,
          per_cohort=False, overrides=None, link_budget=None, model=None):
    """force: a fixed set of unmonitored transformers that must get nodes (and
    no others): the cheapest gateways for them, budget unlimited.
    per_cohort: the group-representative baseline: at most one new node per
    manufacturer + rating cohort with no monitored unit, as many cohorts as
    range and capacity allow, then risk, then cost; budget unlimited."""
    risks = risks or riskmod.static_risks(fleet)
    m = model or Model(fleet, node_scale, gw_scale, radius_scale, overrides, link_budget)
    T, G = m.T, m.G
    P = [(i, j) for i, a in enumerate(T) for j, g in enumerate(G) if m.ok[a.id, g.id]]
    nT, nG, nP = len(T), len(G), len(P)
    n = nT + nG + nP
    r = np.array([0.0 if a.monitored else risks[a.id]["risk"] for a in T])
    cn = np.array([m.ncost[a.id] for a in T])
    cg = np.array([m.gcost[g.id] for g in G])
    c = np.concatenate([-r + EPS_COST * cn, EPS_COST * cg, np.zeros(nP)])
    for p, (i, j) in enumerate(P):
        if T[i].monitored and T[i].gateway != G[j].id:
            c[nT + nG + p] = EPS_MOVE
    rows, lo, hi = [], [], []
    A = lambda: np.zeros(n)
    by_t = {i: [] for i in range(nT)}
    by_g = {j: [] for j in range(nG)}
    for p, (i, j) in enumerate(P):
        by_t[i].append(p); by_g[j].append(p)
    for i in range(nT):                                   # sum_g x_tg = z_t
        a = A(); a[i] = -1
        for p in by_t[i]:
            a[nT + nG + p] = 1
        rows.append(a); lo.append(0); hi.append(0)
    for p, (i, j) in enumerate(P):                        # x_tg <= y_g
        a = A(); a[nT + nG + p] = 1; a[nT + j] = -1
        rows.append(a); lo.append(-np.inf); hi.append(0)
    for j, g in enumerate(G):                             # capacity
        a = A()
        for p in by_g[j]:
            a[nT + nG + p] = 1
        rows.append(a); lo.append(-np.inf); hi.append(m.cap[g.id])
    lb, ub = np.zeros(n), np.ones(n)
    for i, t in enumerate(T):
        if t.monitored:
            # installed nodes stay whenever range and capacity allow: a reward far
            # above any risk total, not a hard constraint, so the program is always
            # feasible and a node that cannot be placed is reported, not hidden
            c[i] -= KEEP
            if not by_t[i]:
                ub[i] = 0
    for j, g in enumerate(G):
        if g.existing:
            lb[nT + j] = 1                                # existing gateways are built
    cap_budget = float(budget)
    if per_cohort:
        covered = {a.cohort for a in fleet.monitored}
        groups = {}
        for i, t in enumerate(T):
            if not t.monitored:
                if t.cohort in covered:
                    ub[i] = 0
                else:
                    groups.setdefault(t.cohort, []).append(i)
        for idx in groups.values():
            a = A(); a[idx] = 1
            rows.append(a); lo.append(-np.inf); hi.append(1)
        unm = np.array([not t.monitored for t in T])
        c[:nT] = np.where(unm, -(1000.0 + r) + EPS_COST * cn, c[:nT])     # installed nodes keep their reward
        cap_budget = np.inf
    if force is not None:
        for i, t in enumerate(T):
            if not t.monitored:
                lb[i] = ub[i] = 1.0 if t.id in force else 0.0
        c[:nT] = np.where([not t.monitored for t in T], 0.0, c[:nT])
        cap_budget = np.inf
    a = A(); a[:nT] = cn; a[nT:nT + nG] = cg              # budget
    rows.append(a); lo.append(-np.inf); hi.append(cap_budget)
    res = milp(c, constraints=LinearConstraint(np.array(rows), lo, hi), integrality=np.ones(n),
               bounds=Bounds(lb, ub), options=MIP_OPTIONS)
    if res.status != 0 or res.x is None:
        return {"feasible": False, "kind": "optimised", "budget": float(budget),
                "message": f"No feasible plan under these assumptions: the solver reported '{res.message}'."}
    x = np.round(res.x).astype(int)
    lost = [T[i].id for i in range(nT) if T[i].monitored and not x[i]]
    nodes = [T[i].id for i in range(nT) if x[i] and not T[i].monitored]
    assign = {T[i].id: G[j].id for p, (i, j) in enumerate(P) if x[nT + nG + p]}
    opened = [G[j].id for j in range(nG) if x[nT + j] and not G[j].existing]
    kind = "cohort" if per_cohort else "optimised" if force is None else "forced"
    return _plan(m, kind, budget, nodes, assign, opened, risks, lost)


def greedy(fleet, budget, node_scale=1.0, gw_scale=1.0, radius_scale=1.0, risks=None, overrides=None,
           link_budget=None, model=None):
    risks = risks or riskmod.static_risks(fleet)
    m = model or Model(fleet, node_scale, gw_scale, radius_scale, overrides, link_budget)
    built = {g.id: g.existing for g in m.G}
    room = dict(m.cap)
    assign, lost = {}, []
    for t in (a for a in m.T if a.monitored):            # current deployment first
        opts = [g.id for g in m.G if built[g.id] and m.ok[t.id, g.id] and room[g.id] > 0]
        if not opts:
            lost.append(t.id)
            continue
        gid = t.gateway if t.gateway in opts else min(opts, key=lambda k: (m.d[t.id][k], k))
        assign[t.id] = gid
        room[gid] -= 1
    left = float(budget)
    nodes, opened = [], []
    todo = {a.id for a in m.T if not a.monitored}
    while True:
        best = None
        for aid in sorted(todo):
            opts = [(0.0, g.id) for g in m.G if built[g.id] and room[g.id] > 0 and m.ok[aid, g.id]]
            opts += [(m.gcost[g.id], g.id) for g in m.G if not built[g.id] and room[g.id] > 0 and m.ok[aid, g.id]]
            if not opts:
                continue
            extra, gid = min(opts)
            cost = m.ncost[aid] + extra
            if cost > left + 1e-6:
                continue
            ratio = risks[aid]["risk"] / cost if cost > 0 else np.inf
            if best is None or ratio > best[0] + 1e-12:
                best = (ratio, aid, gid, cost)
        if best is None:
            break
        _, aid, gid, cost = best
        if not built[gid]:
            built[gid] = True
            opened.append(gid)
        room[gid] -= 1
        nodes.append(aid); assign[aid] = gid; todo.discard(aid)
        left -= cost
    return _plan(m, "greedy", budget, sorted(nodes), assign, opened, risks, lost)


def current(fleet, risks=None, overrides=None, link_budget=None, model=None, **scales):
    """The CURRENT deployment: existing gateways and installed nodes, nothing
    added, under the same assumptions."""
    return solve(fleet, 0.0, risks=risks, force=set(), overrides=overrides, link_budget=link_budget,
                 model=model, **scales) | {"kind": "current"}


# ------------------------------------------------------------------ outputs
def _plan(m, kind, budget, nodes, assign, opened, risks, lost):
    fleet = m.fleet
    gmap = {g.id: g for g in m.G}
    capital = sum(m.ncost[a] for a in nodes) + sum(m.gcost[g] for g in opened)
    annual = sum(fleet.assets[a].node_annual_inr for a in nodes) + sum(gmap[g].annual_inr for g in opened)
    built = [g.id for g in m.G if g.existing] + sorted(opened)
    load = {g: sum(1 for v in assign.values() if v == g) for g in built}
    instrumented = sorted(assign)
    covered = sorted(a.id for a in m.T if any(m.ok[a.id, g] for g in built))
    uncovered = sorted(a.id for a in m.T if a.id not in set(covered))
    high = sorted(aid for aid, rk in risks.items() if rk["band"] == "high")
    N = len(m.T)
    plan = {
        "feasible": True, "kind": kind, "budget": float(budget),
        "nodes": nodes, "assign": assign, "gateways_opened": sorted(opened), "gateways_built": built,
        "capital_inr": int(round(float(capital))), "annual_inr": int(round(float(annual))),
        "within_budget": bool(capital <= float(budget) + 1e-6),
        "risk_covered": round(float(sum(risks[a]["risk"] for a in nodes)), 1),
        "risk_total": round(float(sum(rk["risk"] for rk in risks.values())), 1),
        "high_total": len(high), "high_covered": sum(1 for a in high if a in nodes),
        "n_transformers": N, "n_instrumented": len(instrumented),
        "pct_instrumented": round(100.0 * len(instrumented) / N, 1),
        "n_within_coverage": len(covered), "pct_within_coverage": round(100.0 * len(covered) / N, 1),
        "uncovered": uncovered,
        "reassigned": sorted(a for a, g in assign.items() if fleet.assets[a].monitored and fleet.assets[a].gateway != g),
        "lost_coverage": sorted(lost),
        "gateway_load": [{"id": g, "used": load[g], "capacity": m.cap[g], "existing": gmap[g].existing,
                          "planned": g in opened} for g in built],
        "assumptions": {**m.scales, "link_budget": m.use_lb, "overrides": m.overrides},
        "distance_m": {a: round(m.d[a][g]) for a, g in assign.items()},
    }
    plan["uncovered_high"] = why_not(m, plan, risks, load, built)
    plan["reasons"] = reasons(m, plan, risks)
    if not nodes and kind in ("optimised", "greedy"):
        room = [m.ncost[a.id] for a in m.T if not a.monitored
                for g in built if m.ok[a.id, g] and load[g] < m.cap[g]]
        unbuilt = [m.ncost[a.id] + m.gcost[g.id] for a in m.T if not a.monitored
                   for g in m.G if g.id not in built and m.ok[a.id, g.id]]
        need = min(room) if room else min(unbuilt) if unbuilt else None
        plan["message"] = (f"No transformer can be added within this budget: the cheapest addition costs {rupees(need)}."
                           if need is not None and need > budget else "Nothing feasible to add under these assumptions.")
    return plan


def why_not(m, plan, risks, load, built):
    """Each high-risk transformer left without a node, with the binding reason."""
    out = []
    left = plan["budget"] - plan["capital_inr"]
    for aid, rk in sorted(risks.items(), key=lambda kv: (-kv[1]["risk"], kv[0])):
        if rk["band"] != "high" or aid in plan["nodes"]:
            continue
        feas = [g for g in m.G if m.ok[aid, g.id]]
        if not feas:
            near = min(m.G, key=lambda g: (m.d[aid][g.id], g.id))
            in_radius = [g for g in m.G if m.d[aid][g.id] <= m.radius[g.id]]
            why = (f"out of range of every site: nearest is {near.id} at {m.d[aid][near.id] / 1000:.2f} km, "
                   f"assumed radius {m.radius[near.id] / 1000:.1f} km") if not in_radius else \
                  (f"within radius of {', '.join(g.id for g in in_radius)} but the estimated link margin is below "
                   f"the required {m.lb['required_margin_db']:g} dB")
            out.append({"id": aid, "risk": rk["risk"], "reason": "range", "text": why})
            continue
        open_ = [g for g in feas if g.id in built]
        room = [g for g in open_ if load[g.id] < m.cap[g.id]]
        closed = [g for g in feas if g.id not in built]
        if open_ and not room and not closed:
            out.append({"id": aid, "risk": rk["risk"], "reason": "capacity",
                        "text": "gateway full: " + ", ".join(f"{g.id} ({load[g.id]}/{m.cap[g.id]})" for g in open_)})
            continue
        need = m.ncost[aid] + (0 if room else min(m.gcost[g.id] for g in closed))
        via = room[0].id if room else min(closed, key=lambda g: (m.gcost[g.id], g.id)).id
        out.append({"id": aid, "risk": rk["risk"], "reason": "budget",
                    "text": f"budget: needs {rupees(need)} via {via} ({'node only' if room else 'node + new gateway'}); "
                            f"{rupees(max(left, 0))} left unspent"})
    return out


def reasons(m, plan, risks):
    """One template sentence per recommendation, from the plan itself."""
    out = []
    existing = [g for g in m.G if g.existing]
    for gid in plan["gateways_opened"]:
        mine = sorted(a for a, g in plan["assign"].items() if g == gid and a in plan["nodes"])
        hi = [a for a in mine if risks[a]["band"] == "high"]
        alone = [a for a in mine if not any(m.ok[a, e.id] for e in existing)]
        why = ("out of range of all existing gateways" if len(alone) == len(mine)
               else f"{len(alone)} of them out of range of all existing gateways, the rest beyond existing capacity"
               if alone else "existing gateways in range are full")
        names = ", ".join(mine[:6]) + (f" and {len(mine) - 6} more" if len(mine) > 6 else "")
        out.append({"kind": "gateway", "id": gid, "cost_inr": round(m.gcost[gid]),
                    "text": f"Build {gid} ({rupees(m.gcost[gid])}): unlocks {names}"
                            + (f" ({', '.join(hi)} high risk)" if hi else "") + f", {why}."})
    for aid in plan["nodes"]:
        g = plan["assign"][aid]
        rk = risks[aid]
        out.append({"kind": "node", "id": aid, "cost_inr": round(m.ncost[aid]),
                    "text": f"Instrument {aid} ({rupees(m.ncost[aid])}): risk {rk['risk']:.0f} ({rk['band']}), "
                            f"{rk['driver'].split('; ')[-1]}; via {g} at {m.d[aid][g] / 1000:.2f} km"
                            + (" (planned)" if g in plan["gateways_opened"] else "") + "."})
    for aid in plan["reassigned"]:
        out.append({"kind": "move", "id": aid, "cost_inr": 0,
                    "text": f"Move {aid} from {m.fleet.assets[aid].gateway} to {plan['assign'][aid]}: "
                            "its current gateway is no longer feasible or is needed for higher-risk units."})
    for aid in plan["lost_coverage"]:
        out.append({"kind": "lost", "id": aid, "cost_inr": 0,
                    "text": f"{aid} would lose coverage under these assumptions: no feasible built or buildable gateway."})
    return out


def compare(opt, gr):
    """Why the optimised plan beats (or matches) the greedy one, from their
    outputs only."""
    if not opt.get("feasible") or not gr.get("feasible"):
        return opt.get("message") or gr.get("message")
    d_risk = opt["risk_covered"] - gr["risk_covered"]
    if abs(d_risk) < 0.05 and set(opt["nodes"]) == set(gr["nodes"]):
        return "At this budget the greedy baseline finds the same plan."
    only_o = sorted(set(opt["nodes"]) - set(gr["nodes"]))
    only_g = sorted(set(gr["nodes"]) - set(opt["nodes"]))
    parts = [f"Covers {opt['risk_covered']:.0f} risk points against {gr['risk_covered']:.0f} for the greedy "
             f"baseline ({'+' if d_risk >= 0 else ''}{d_risk:.0f}), {opt['high_covered']} of {opt['high_total']} "
             f"high-risk units against {gr['high_covered']}."]
    if opt["gateways_opened"] != gr["gateways_opened"]:
        parts.append(f"It builds {', '.join(opt['gateways_opened']) or 'no new gateway'} where greedy builds "
                     f"{', '.join(gr['gateways_opened']) or 'none'}. Greedy judges each unit alone, so a gateway "
                     "opens only when one unit's own risk per rupee pays for it; the optimiser counts every unit "
                     "the gateway would then reach.")
    if only_o:
        parts.append(f"Optimiser adds {', '.join(only_o)}; greedy instead adds {', '.join(only_g) or 'nothing more'}.")
    return " ".join(parts)


def bundle(fleet, budget, overrides=None, link_budget=None, risks=None):
    """What the map shows for one set of assumptions: optimised, greedy and
    current plans, the comparison sentence, and every transformer x gateway
    pair with its separate fields (capacity as loaded by the optimised plan)."""
    m = Model(fleet, overrides=overrides, link_budget=link_budget)
    risks = risks or riskmod.static_risks(fleet)
    opt = solve(fleet, budget, risks=risks, model=m)
    gr = greedy(fleet, budget, risks=risks, model=m)
    cur = current(fleet, risks=risks, model=m)
    show = opt if opt["feasible"] else cur
    load = {g["id"]: g["used"] for g in show["gateway_load"]}
    room = {g.id: (load.get(g.id, 0) < m.cap[g.id]) for g in m.G}
    return {"budget": float(budget), "optimised": opt, "greedy": gr, "current": cur,
            "why": compare(opt, gr) if opt["feasible"] else opt["message"],
            "pairs": {a.id: m.pairs_for(a.id, room) for a in m.T},
            "radius_m": m.radius, "cost_inr": m.install_cost, "max_nodes": m.cap,
            "link_budget": m.use_lb, "risks": risks}


SENS = [("node_scale", "Node cost"), ("gw_scale", "Gateway cost"), ("radius_scale", "Coverage radius")]


def sensitivity(fleet, budget, base=None, overrides=None, link_budget=None):
    base = base or solve(fleet, budget, overrides=overrides, link_budget=link_budget)
    out = []
    for key, label in SENS:
        for f in (0.8, 1.2):
            p = solve(fleet, budget, overrides=overrides, link_budget=link_budget, **{key: f})
            if not p["feasible"]:
                out.append({"assumption": label, "change": f"{f - 1:+.0%}", "feasible": False})
                continue
            changed = sorted(set(p["nodes"]) ^ set(base["nodes"])) + sorted(set(p["gateways_opened"]) ^ set(base["gateways_opened"]))
            out.append({"assumption": label, "change": f"{f - 1:+.0%}", "feasible": True,
                        "changed": changed, "n_changed": len(changed),
                        "risk_covered": p["risk_covered"], "d_risk": round(p["risk_covered"] - base["risk_covered"], 1),
                        "nodes": len(p["nodes"]), "gateways_opened": p["gateways_opened"]})
    agg = {}
    for o in out:
        if o.get("feasible"):
            k = agg.setdefault(o["assumption"], [0, 0.0])
            k[0] += o["n_changed"]; k[1] += abs(o["d_risk"])
    top = max(agg.items(), key=lambda kv: (kv[1][0], kv[1][1], kv[0]))[0] if agg else None
    if top and agg[top][0] == 0 and agg[top][1] == 0:
        verdict = "No ±20 % change in node cost, gateway cost or coverage radius changes the plan."
    else:
        verdict = (f"{top} changes the plan most: ±20 % moves {agg[top][0]} choices in total and "
                   f"{agg[top][1]:.0f} risk points. Get this number from procurement or a radio survey first.")
    return {"rows": out, "most_sensitive": top, "verdict": verdict}

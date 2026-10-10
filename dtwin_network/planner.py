"""
Deployment planner: which unmonitored transformers get a node, and which
candidate gateway sites open, for a capital budget.

Mixed-integer program (scipy.optimize.milp, HiGHS):

  variables   x_i in {0,1}   node on unmonitored transformer i
              y_g in {0,1}   candidate gateway site g opens (existing: fixed open)
              z_ig in {0,1}  node i reports through gateway g; only pairs with
                             distance(i, g) <= coverage radius exist
  maximise    sum_i r_i x_i  -  eps * capital cost       (r_i = risk score;
                             eps breaks ties toward the cheaper plan, never
                             against risk: eps * max cost < 0.01 risk point)
  subject to  sum_g z_ig = x_i                     each node has one gateway in range
              z_ig <= y_g                          only through an open gateway
              sum_i z_ig <= capacity_g - used_g    gateway capacity (nodes)
              sum_i c_i x_i + sum_g C_g y_g <= B   capital budget, never exceeded

The greedy baseline adds, one at a time, the transformer with the highest risk
per rupee of what it costs to add now (its node, plus the cheapest gateway in
range if none open has room), while it fits the budget.

The risk score uses metadata only (dtwin_network/risk.py), so the plan does not
move with the live state; the same input always gives the same plan.
"""

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from . import risk as riskmod
from .fleet import dist

EPS = 1e-9


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


def _setup(fleet, node_scale=1.0, gw_scale=1.0, radius_scale=1.0):
    U = sorted(fleet.unmonitored, key=lambda a: a.id)
    G = sorted(fleet.gateways.values(), key=lambda g: (not g.existing, g.id))
    used = {g.id: sum(1 for a in fleet.monitored if a.gateway == g.id) for g in G}
    pairs = [(i, j) for i, a in enumerate(U) for j, g in enumerate(G) if fleet.in_range(a, g, radius_scale)]
    cost_n = np.array([a.node_cost_inr * node_scale for a in U])
    cost_g = np.array([0.0 if g.existing else g.cost_inr * gw_scale for g in G])
    return U, G, used, pairs, cost_n, cost_g


def solve(fleet, budget, node_scale=1.0, gw_scale=1.0, radius_scale=1.0, risks=None, force=None,
          per_cohort=False):
    """force: a fixed set of transformers that must get nodes (and no others);
    the program then finds the cheapest gateways for them, budget unlimited.
    per_cohort: the group-representative baseline. At most one node in each
    manufacturer + rating cohort that has no monitored unit, none elsewhere;
    represent as many cohorts as range and capacity allow, then the higher
    risk, then the lower cost. Budget unlimited."""
    risks = risks or riskmod.static_risks(fleet)
    U, G, used, pairs, cost_n, cost_g = _setup(fleet, node_scale, gw_scale, radius_scale)
    nU, nG, nP = len(U), len(G), len(pairs)
    r = np.array([risks[a.id]["risk"] for a in U])
    n = nU + nG + nP
    c = np.concatenate([-r + EPS * cost_n, EPS * cost_g, np.zeros(nP)])
    rows, lo, hi = [], [], []

    def row():
        return np.zeros(n)

    for i in range(nU):                                  # sum_g z_ig - x_i = 0
        a = row(); a[i] = -1
        for p, (pi, pj) in enumerate(pairs):
            if pi == i:
                a[nU + nG + p] = 1
        rows.append(a); lo.append(0); hi.append(0)
    for p, (pi, pj) in enumerate(pairs):                 # z_ig - y_g <= 0
        a = row(); a[nU + nG + p] = 1; a[nU + pj] = -1
        rows.append(a); lo.append(-np.inf); hi.append(0)
    for j, g in enumerate(G):                            # capacity
        a = row()
        for p, (pi, pj) in enumerate(pairs):
            if pj == j:
                a[nU + nG + p] = 1
        rows.append(a); lo.append(-np.inf); hi.append(max(g.capacity - used[g.id], 0))
    if per_cohort:
        covered = {a.cohort for a in fleet.monitored}
        groups = {}
        for i, u in enumerate(U):
            if u.cohort not in covered:
                groups.setdefault(u.cohort, []).append(i)
        for idx in groups.values():                      # one per cohort
            a = row(); a[idx] = 1
            rows.append(a); lo.append(-np.inf); hi.append(1)
        c[:nU] = -(1000.0 + r) + EPS * cost_n
    a = row(); a[:nU] = cost_n; a[nU:nU + nG] = cost_g   # budget
    rows.append(a); lo.append(-np.inf); hi.append(float(budget))
    lb = np.zeros(n); ub = np.ones(n)
    for j, g in enumerate(G):
        if g.existing:
            lb[nU + j] = 1                               # existing gateways are open
    if per_cohort:
        hi[-1] = np.inf                                  # not limited by the budget
        for i, u in enumerate(U):
            if u.cohort in {a.cohort for a in fleet.monitored}:
                ub[i] = 0.0
    if force is not None:
        for i, a in enumerate(U):
            lb[i] = ub[i] = 1.0 if a.id in force else 0.0
        c[:nU] = 0.0
        c[nU:nU + nG] = cost_g                           # cheapest gateways that reach them
        hi[-1] = np.inf
    res = milp(c, constraints=LinearConstraint(np.array(rows), lo, hi), integrality=np.ones(n),
               bounds=Bounds(lb, ub), options={"disp": False, "presolve": True})
    if res.status != 0 or res.x is None:
        return {"feasible": False, "message": f"No feasible plan: the solver reported '{res.message}'.",
                "budget": budget}
    x = np.round(res.x).astype(int)
    nodes = [U[i].id for i in range(nU) if x[i]]
    assign = {U[pi].id: G[pj].id for p, (pi, pj) in enumerate(pairs) if x[nU + nG + p]}
    opened = [G[j].id for j in range(nG) if x[nU + j] and not G[j].existing]
    return _plan(fleet, "cohort" if per_cohort else "optimised" if force is None else "forced", budget, nodes, assign, opened, risks, node_scale, gw_scale, radius_scale)


def greedy(fleet, budget, node_scale=1.0, gw_scale=1.0, radius_scale=1.0, risks=None):
    risks = risks or riskmod.static_risks(fleet)
    U, G, used, pairs, cost_n, cost_g = _setup(fleet, node_scale, gw_scale, radius_scale)
    room = {g.id: g.capacity - used[g.id] for g in G}
    is_open = {g.id: g.existing for g in G}
    gcost = {g.id: cg for g, cg in zip(G, cost_g)}
    reach = {a.id: [G[pj] for pi, pj in pairs if U[pi].id == a.id] for a in U}
    ncost = {a.id: cn for a, cn in zip(U, cost_n)}
    left, spent = float(budget), 0.0
    nodes, assign, opened = [], {}, []
    todo = {a.id for a in U}
    while True:
        best = None
        for aid in sorted(todo):
            opts = [(0.0, g.id) for g in reach[aid] if is_open[g.id] and room[g.id] > 0]
            opts += [(gcost[g.id], g.id) for g in reach[aid] if not is_open[g.id] and room[g.id] > 0]
            if not opts:
                continue
            extra, gid = min(opts)
            cost = ncost[aid] + extra
            if cost > left + 1e-6:
                continue
            ratio = risks[aid]["risk"] / cost if cost > 0 else np.inf
            if best is None or ratio > best[0] + 1e-12:
                best = (ratio, aid, gid, cost, extra)
        if best is None:
            break
        _, aid, gid, cost, extra = best
        if not is_open[gid]:
            is_open[gid] = True
            opened.append(gid)
        room[gid] -= 1
        nodes.append(aid); assign[aid] = gid; todo.discard(aid)
        left -= cost; spent += cost
    return _plan(fleet, "greedy", budget, sorted(nodes), assign, opened, risks, node_scale, gw_scale, radius_scale)


def _plan(fleet, kind, budget, nodes, assign, opened, risks, ns, gs, rs):
    U, G, used, pairs, cost_n, cost_g = _setup(fleet, ns, gs, rs)
    ncost = {a.id: cn for a, cn in zip(U, cost_n)}
    gmap = {g.id: g for g in G}
    capital = sum(ncost[a] for a in nodes) + sum(gmap[g].cost_inr * gs for g in opened)
    annual = sum(fleet.assets[a].node_annual_inr for a in nodes) + sum(gmap[g].annual_inr for g in opened)
    load = {g.id: used[g.id] + sum(1 for a in nodes if assign.get(a) == g.id) for g in G}
    open_ids = [g.id for g in G if g.existing] + list(opened)
    high = [aid for aid, rk in risks.items() if rk["band"] == "high"]
    covered_high = [a for a in high if a in nodes]
    plan = {
        "feasible": True, "kind": kind, "budget": float(budget),
        "nodes": nodes, "assign": assign, "gateways_opened": sorted(opened),
        "capital_inr": int(round(float(capital))), "annual_inr": int(round(float(annual))),
        "within_budget": bool(capital <= budget + 1e-6),
        "risk_covered": round(float(sum(risks[a]["risk"] for a in nodes)), 1),
        "risk_total": round(sum(rk["risk"] for rk in risks.values()), 1),
        "high_total": len(high), "high_covered": len(covered_high),
        "gateway_load": [{"id": g, "used": load[g], "capacity": gmap[g].capacity, "existing": gmap[g].existing}
                         for g in open_ids],
        "assumptions": {"node_cost_scale": ns, "gateway_cost_scale": gs, "radius_scale": rs},
    }
    plan["uncovered_high"] = _why_not(fleet, plan, risks, ns, gs, rs, load, open_ids)
    if not nodes:
        reachable = [ncost[U[pi].id] for pi, pj in pairs if G[pj].existing and load[G[pj].id] < G[pj].capacity]
        need = min(reachable) if reachable else min(cost_n[i] + cost_g[j] for i, j in pairs) if pairs else None
        plan["message"] = ("No transformer can be added within this budget: the cheapest addition costs "
                           f"{rupees(need)}." if need is not None and need > budget else "Nothing to add.")
    return plan


def _why_not(fleet, plan, risks, ns, gs, rs, load, open_ids):
    out = []
    left = plan["budget"] - plan["capital_inr"]
    for aid, rk in sorted(risks.items(), key=lambda kv: -kv[1]["risk"]):
        if rk["band"] != "high" or aid in plan["nodes"]:
            continue
        a = fleet.assets[aid]
        in_range = [g for g in fleet.gateways.values() if fleet.in_range(a, g, rs)]
        if not in_range:
            near = min(fleet.gateways.values(), key=lambda g: dist(a.xy, g.xy))
            out.append({"id": aid, "risk": rk["risk"], "reason": "range",
                        "text": f"out of range: {dist(a.xy, near.xy):.1f} km from the nearest gateway site "
                                f"({near.id}), beyond the {near.radius_km * rs:.1f} km coverage radius"})
            continue
        open_in = [g for g in in_range if g.id in open_ids]
        room = [g for g in open_in if load[g.id] < g.capacity]
        closed = [g for g in in_range if g.id not in open_ids]
        if open_in and not room and not closed:
            out.append({"id": aid, "risk": rk["risk"], "reason": "capacity",
                        "text": "capacity: " + ", ".join(f"{g.id} full ({load[g.id]}/{g.capacity})" for g in open_in)})
            continue
        need = a.node_cost_inr * ns + (0 if room else min(g.cost_inr * gs for g in closed))
        via = room[0].id if room else min(closed, key=lambda g: g.cost_inr).id
        out.append({"id": aid, "risk": rk["risk"], "reason": "budget",
                    "text": f"budget: needs {rupees(need)} via {via}"
                            + (f" ({'node only' if room else 'node + new gateway'}); {rupees(max(left, 0))} left unspent")})
    return out


def compare(opt, gr):
    """Why the optimised plan beats (or matches) the greedy one, from their
    outputs only."""
    d_risk = opt["risk_covered"] - gr["risk_covered"]
    if abs(d_risk) < 0.05 and set(opt["nodes"]) == set(gr["nodes"]):
        return "At this budget the greedy baseline finds the same plan."
    only_o = sorted(set(opt["nodes"]) - set(gr["nodes"]))
    only_g = sorted(set(gr["nodes"]) - set(opt["nodes"]))
    parts = [f"Covers {opt['risk_covered']:.0f} risk points against {gr['risk_covered']:.0f} for the greedy "
             f"baseline ({'+' if d_risk >= 0 else ''}{d_risk:.0f}), {opt['high_covered']} of {opt['high_total']} "
             f"high-risk units against {gr['high_covered']}."]
    if opt["gateways_opened"] != gr["gateways_opened"]:
        parts.append(f"It opens {', '.join(opt['gateways_opened']) or 'no new gateway'} where greedy opens "
                     f"{', '.join(gr['gateways_opened']) or 'none'}. Greedy judges each unit alone, so a gateway "
                     "opens only when one unit's own risk per rupee pays for it; the optimiser counts every unit "
                     "the gateway would then reach.")
    if only_o:
        parts.append(f"Optimiser adds {', '.join(only_o)}; greedy instead adds {', '.join(only_g) or 'nothing more'}.")
    return " ".join(parts)


SENS = [("node_scale", "Node cost"), ("gw_scale", "Gateway cost"), ("radius_scale", "Coverage radius")]


def sensitivity(fleet, budget, base=None):
    base = base or solve(fleet, budget)
    out = []
    for key, label in SENS:
        for f in (0.8, 1.2):
            p = solve(fleet, budget, **{key: f})
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

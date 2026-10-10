"""
Strategy comparison at one capital budget. Every number comes from the fleet
file and the planner; the wording is a fixed template.

  A  current monitoring           the 12 installed nodes, nothing added
  B  risk-prioritised nodes       greedy risk-per-rupee (planner.greedy)
  C  optimised, gateway-shared    the MILP plan (planner.solve)
  D  group representative         the installed nodes, plus one node in every
                                  manufacturer + rating cohort that has none
                                  (as many cohorts as range and capacity allow,
                                  then highest risk, then least cost; chosen
                                  by the same MILP). Not limited by the
                                  budget: its cost is what it is.

"High priority" is the high band of the metadata risk score among the
unmonitored transformers (dtwin_network/risk.py), the same set the planner
works on.
"""

from . import planner, risk as riskmod

CAPTION = ("D is a comparison scenario. Type testing proves a design; in-service monitoring tracks each unit's "
           "own loading, cooling and condition. This is not a claim that group testing is ineffective.")


def _row(key, name, fleet, plan, risks, note=""):
    open_load = plan["gateway_load"]
    used = sum(g["used"] for g in open_load)
    cap = sum(g["capacity"] for g in open_load)
    gw_annual = sum(fleet.gateways[g["id"]].annual_inr for g in open_load)
    mon_annual = sum(a.node_annual_inr for a in fleet.monitored)
    n_mon = len(fleet.monitored) + len(plan["nodes"])
    return {"key": key, "name": name, "monitored": n_mon,
            "initial_inr": plan["capital_inr"],
            "recurring_inr": round(plan["annual_inr"] + mon_annual
                                   + sum(fleet.gateways[g].annual_inr for g in fleet.gateways if fleet.gateways[g].existing)),
            "risk_covered": plan["risk_covered"], "risk_total": plan["risk_total"],
            "high_monitored": plan["high_covered"], "high_total": plan["high_total"],
            "unmonitored": len(fleet.assets) - n_mon,
            "gateway_used": used, "gateway_capacity": cap,
            "gateway_util_pct": round(100.0 * used / cap) if cap else None,
            "gateways_opened": plan["gateways_opened"], "nodes_added": plan["nodes"],
            "within_budget": plan["within_budget"], "note": note}


def compare(fleet, budget):
    risks = riskmod.static_risks(fleet)
    none = planner.solve(fleet, budget, risks=risks, force=set())
    gr = planner.greedy(fleet, budget, risks=risks)
    op = planner.solve(fleet, budget, risks=risks)
    cohorts = {}
    for a in fleet.assets.values():
        cohorts.setdefault(a.cohort, []).append(a)
    open_cohorts = {c for c, m in cohorts.items() if not any(a.monitored for a in m)}
    d = planner.solve(fleet, budget, risks=risks, per_cohort=True)
    if not d["feasible"]:          # capacity can make a forced set impossible; say so
        d_row = {"key": "D", "name": "Group representative", "feasible": False, "note": d["message"]}
    else:
        d_row = _row("D", "Group representative (one per cohort)", fleet, d, risks,
                     note=(f"{len(cohorts)} cohorts, {len(cohorts) - len(open_cohorts)} already monitored; "
                           f"{len(d['nodes'])} of {len(open_cohorts)} others get a representative"
                           + ("" if len(d["nodes"]) == len(open_cohorts) else " (the rest: no unit in range or no capacity)")
                           + ("" if d["capital_inr"] <= budget else "; costs more than the budget")))
    rows = [_row("A", "Current monitoring", fleet, none, risks, note="installed nodes only; cost already spent"),
            _row("B", "Risk-prioritised individual nodes (greedy)", fleet, gr, risks),
            _row("C", "Optimised gateway-shared plan", fleet, op, risks),
            d_row]
    return {"budget": float(budget), "rows": rows, "caption": CAPTION, "cohorts": len(cohorts),
            "high_total": op["high_total"],
            "disclaimer": fleet.district.get("disclaimer")}

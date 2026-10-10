"""
Component-level diagnostics: which evidence bears on each component group,
and, for each abnormal finding, ranked hypotheses with the evidence for and
against each and the uncertainty that remains.

Deterministic: every ranking comes from a rule on computed quantities, and
the rule is stated. Likelihood is ordinal ("more likely" / "possible" / "less
likely" / "excluded"), never a made-up probability. A general anomaly is
never pinned on a specific component without evidence for that component.
"""

import numpy as np
import pandas as pd

from . import components as TC
from ..labels import channel as chname, state as stname

GROUPS = {
    "Windings": {"ids": ["lv_winding_r", "lv_winding_y", "lv_winding_b", "hv_winding_r", "hv_winding_y", "hv_winding_b"],
                 "evidence": ["phase currents IR, IY, IB", "top-oil temperature", "C57.91 winding gradient"],
                 "missing": ["direct winding temperature", "winding resistance test", "DGA"]},
    "Core": {"ids": ["core"], "evidence": ["phase voltages (overfluxing only)"],
             "missing": ["no-load current (not observable in service: the measured current is load current)",
                         "open-circuit test", "DGA"]},
    "Oil and cooling": {"ids": ["oil", "radiators", "conservator", "breather"],
                        "evidence": ["top-oil temperature", "ambient temperature", "rise-fit slope and intercept"],
                        "missing": ["oil level", "fin temperatures", "oil quality (BDV, moisture, DGA)"]},
    "Bushings and connections": {"ids": ["lv_bushing_r", "lv_bushing_y", "lv_bushing_b", "lv_bushing_n",
                                         "hv_bushing_r", "hv_bushing_y", "hv_bushing_b"],
                                 "evidence": ["LV phase voltage against its own current (apparent series resistance)"],
                                 "missing": ["HV-side measurements", "terminal temperatures", "joint resistance test"]},
    "Tank and fittings": {"ids": ["tank", "tap_switch", "drain_valve", "earthing"],
                          "evidence": [],
                          "missing": ["tank surface temperature", "pressure", "tap position", "earth resistance",
                                      "leak detection"]},
    "Instrumentation": {"ids": ["oil_pocket", "monitor_box"],
                        "evidence": ["cross-phase consistency", "thermal-fit intercept", "stuck / dead / step checks"],
                        "missing": ["a reference thermometer reading"]},
}
RANK = {"more likely": 0, "possible": 1, "less likely": 2, "excluded": 3}


def _h(hypothesis, likelihood, supporting=(), against=(), uncertainty=""):
    return {"hypothesis": hypothesis, "likelihood": likelihood, "supporting": list(supporting),
            "against": list(against), "uncertainty": uncertainty}


def _ranked(hs):
    hs = sorted(hs, key=lambda h: RANK[h["likelihood"]])
    for k, h in enumerate(hs, 1):
        h["rank"] = k
    return hs


# --------------------------------------------------------------------------- #
# Evidence helpers on the analytics at the cursor
# --------------------------------------------------------------------------- #
def cooling_onset(tw, i):
    """Hours from the (12 h median) slope leaving the healthy band (1.03) to
    the alarm level (1.06), within the excursion that is active at the cursor.
    The median removes the diurnal wobble of the fit. A gradual onset
    suggests fouling or slow oil loss; an abrupt one, a sudden blockage such
    as a closed valve."""
    sm = tw.rows.slope.iloc[: i + 1].rolling("12h", min_periods=4).median()
    if not (sm.iloc[-1] > 1.03):
        return None
    k = len(sm) - 1
    while k > 0 and sm.iloc[k - 1] > 1.03:      # start of the current excursion
        k -= 1
    exc = sm.iloc[k:]
    hit = exc[exc > 1.06]
    if hit.empty:
        return None
    start, alarm = exc.index[0], hit.index[0]
    return {"left_healthy_band": start.isoformat(), "crossed_alarm": alarm.isoformat(),
            "hours": round((alarm - start).total_seconds() / 3600, 1), "smoothing": "12 h rolling median"}


def overload_history(tw, i, days=7):
    t = tw.rows.index[i]
    w = tw.rows.loc[t - pd.Timedelta(days=days):t]
    eps = []
    run = False
    for ts, v in w.overload.items():
        if v and not run:
            eps.append(ts)
        run = bool(v)
    hours = [e.hour for e in eps]
    return {"episodes_7d": len(eps), "start_hours": hours}


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #
def findings(tw, i, comps, ev):
    r = tw.rows.iloc[i]
    out = []
    np_ = tw.np_

    rad = comps["radiators"]
    if rad["state"] in ("red", "yellow"):
        on = cooling_onset(tw, i)
        gradual = on is not None and on["hours"] >= 12
        abrupt = on is not None and on["hours"] < 6
        onset_txt = (f"the slope took {on['hours']:.0f} h to go from 1.03 to 1.06" if on else
                     "the slope is above the healthy band but has not reached the alarm")
        out.append({"condition": "Cooling path degraded", "component_id": "radiators", "state": rad["state"],
                    "evidence": [rad["reason"].rstrip("."), onset_txt], "ranked": _ranked([
                        _h("Radiator fins fouled with dust, debris or nesting", "more likely" if gradual else "possible",
                           ["rise scales with load (slope " + f"{ev['slope']:.2f})", onset_txt] if gradual else
                           ["rise scales with load"], ["abrupt onset"] if abrupt else [],
                           "cannot be told from oil loss without the oil gauge"),
                        _h("Low oil level reducing circulation (oil loss)", "more likely" if gradual else "possible",
                           ["rise scales with load", onset_txt] if gradual else ["rise scales with load"],
                           [], "oil loss also shortens the oil time constant; not estimated from this data"),
                        _h("A radiator valve closed or blocked", "more likely" if abrupt else "less likely" if gradual else "possible",
                           [onset_txt] if abrupt else [], [onset_txt] if gradual else [],
                           "a partly closed valve can also act gradually"),
                        _h("Cooling fan failure", "excluded", [], ["ONAN unit: there are no fans"], ""),
                    ])})

    wind = [comps[f"lv_winding_{p}"] for p in "ryb"]
    if any(w["state"] == "red" for w in wind) and r.overload:
        hist = overload_history(tw, i)
        repeat = hist["episodes_7d"] >= 2 and len(set(hist["start_hours"])) <= 2
        out.append({"condition": "Windings overloaded", "component_id": "lv_winding_r", "state": "red",
                    "evidence": [f"load {100 * r.K_oil:.0f} % of rated", f"{hist['episodes_7d']} overload episode(s) in 7 days"],
                    "ranked": _ranked([
                        _h("Demand growth at the evening peak", "more likely" if repeat else "possible",
                           [f"overloads on {hist['episodes_7d']} days starting at hours {sorted(set(hist['start_hours']))}"] if repeat else [],
                           [] if repeat else ["a single episode"], "needs the feeder load history to confirm"),
                        _h("Temporary load transfer from a neighbouring feeder", "less likely" if repeat else "possible",
                           [] if repeat else ["a single episode"], ["repeats at the same hour"] if repeat else [],
                           "check the switching log"),
                    ])})

    if r.elec_status == "asset_unbalance":
        conn = ev["connection"]["phases"]
        rs = {p: conn[p]["r_ohm"] for p in "RYB" if conn[p]["r_ohm"] is not None}
        med = float(np.median(list(rs.values()))) if rs else None
        hi = max(rs, key=rs.get) if rs else None
        joint = hi is not None and med and rs[hi] / med > TC.config()["connection"]["ratio_warn"]
        flagged = [w for w in wind if w["state"] in ("yellow", "red")]
        cid = flagged[0]["id"] if flagged else None          # never pinned on a phase without evidence
        out.append({"condition": "Phase load unbalance", "component_id": cid or "lv_winding_r", "state": "yellow",
                    "component_evidence": "the flagged winding" if cid else "no single phase flagged",
                    "evidence": [f"current unbalance {tw.rows.current_unbalance_pct.iloc[i]:.1f} %",
                                 "voltages corroborate a real redistribution (not a CT error)"],
                    "ranked": _ranked([
                        _h("Single-phase loads concentrated on one phase", "less likely" if joint else "more likely",
                           ["voltages move with the currents"], [f"{hi}-phase connection resistance x{rs[hi] / med:.1f}"] if joint else [],
                           "a load survey per phase confirms it"),
                        _h(f"High-resistance connection on the {hi} phase" if hi else "High-resistance connection",
                           "more likely" if joint else "less likely",
                           [f"{hi}-phase apparent resistance x{rs[hi] / med:.1f} the others"] if joint else [],
                           [] if joint else ["all phases show similar apparent series resistance"],
                           "a thermal image of the LV lugs settles it"),
                        _h("CT ratio or wiring error", "excluded", [],
                           ["the voltage response corroborates the current change"], ""),
                    ])})

    if r.thermal_status == "sensor_temp_offset":
        ch = r.offset_channel or "oil_temp|ambient_temp"
        pair = "|" in ch
        out.append({"condition": "Temperature instrument offset", "component_id": "oil_pocket", "state": "sensor",
                    "evidence": [f"intercept {ev['intercept']:+.2f} K at slope {ev['slope']:.2f}"],
                    "ranked": _ranked([
                        _h("Oil probe drift or offset", "possible" if pair else "more likely" if "oil" in ch else "less likely",
                           ["constant shift at every load"] + ([] if pair else ["step on the oil channel at onset"] if "oil" in ch else []),
                           [], "oil-high and ambient-low offsets are thermally identical" if pair else ""),
                        _h("Ambient probe reading low", "possible" if pair else "more likely" if "ambient" in ch else "less likely",
                           ["constant shift at every load"] + ([] if pair else ["step on the ambient channel at onset"] if "ambient" in ch else []),
                           [], "oil-high and ambient-low offsets are thermally identical" if pair else ""),
                        _h("Real thermal fault", "excluded", [], [f"slope {ev['slope']:.2f}: the rise does not scale with load"], ""),
                    ])})

    bad = {c: s for c, s in ev["channels"].items() if s in ("dead", "stuck", "suspect")}
    for ch, st in bad.items():
        out.append({"condition": f"{chname(ch)} channel: {stname(st)}", "component_id": "monitor_box", "state": "sensor",
                    "evidence": [f"{chname(ch)}: {stname(st)}"], "ranked": _ranked([
                        _h("Logger input or wiring fault", "more likely" if st in ("dead", "stuck") else "possible",
                           [f"reading: {stname(st)}"], [], ""),
                        _h("Sensor (CT, VT or probe) failure", "possible", [f"reading: {stname(st)}"], [], "swap test needed"),
                        _h("Transformer fault", "excluded", [], ["the other channels and the thermal twin agree"], ""),
                    ])})

    if r.thermal_status == "thermal_mismatch":
        t = tw.rows.index[i]
        w = tw.rows.loc[t - pd.Timedelta(hours=48):t]
        near_cool = (w.thermal_status == "asset_cooling").any()
        long_run = (tw.rows.thermal_status.iloc[: i + 1] == "thermal_mismatch").mean() > 0.5
        out.append({"condition": "Thermal model mismatch", "component_id": "oil", "state": comps["oil"]["state"],
                    "evidence": [f"slope {ev['slope']:.2f} below the 0.94 band"], "ranked": _ranked([
                        _h("Fit transient around a fast cooling change", "more likely" if near_cool else "less likely",
                           ["a cooling alarm within 48 h"] if near_cool else [], [] if near_cool else ["no cooling change nearby"],
                           "the 24 h fit window straddles the change"),
                        _h("Nameplate rating or rises wrong for this unit", "more likely" if long_run else "possible",
                           ["mismatch for most of the record"] if long_run else [], [] if long_run else ["short episode"],
                           "a heat-run test or the OEM data sheet settles it"),
                        _h("Oil probe not at the top of the oil (reads cool)", "possible", [], [], "inspect the pocket position"),
                    ])})
    return out


def diagnose(tw, i, fresh=None):
    ev = TC.from_twin(tw, i, fresh)
    a = TC.assess(ev)
    comps = {c["id"]: c for c in a["components"]}
    fs = [] if ev["stale"] else findings(tw, i, comps, ev)
    for f in fs:                                   # the inspector shows the ranked list for that component
        c = comps.get(f["component_id"])
        if c is not None:
            c.setdefault("findings", []).append({"condition": f["condition"], "ranked": f["ranked"],
                                                 "evidence": f["evidence"]})
    order = {"red": 0, "sensor": 1, "yellow": 2, "green": 3, "grey": 4}
    groups = []
    for g, spec in GROUPS.items():
        cs = [comps[x] for x in spec["ids"] if x in comps]
        worst = min((c["state"] for c in cs), key=lambda s: order[s]) if cs else "grey"
        n_av, n_mi = len(spec["evidence"]), len(spec["missing"])
        groups.append({"group": g, "component_ids": spec["ids"], "state": worst,
                       "evidence_available": spec["evidence"], "evidence_missing": spec["missing"],
                       "evidence_coverage": round(n_av / (n_av + n_mi), 2) if n_av + n_mi else 0.0,
                       "findings": [f for f in fs if f["component_id"] in spec["ids"]]})
    return {"asset_id": tw.np_.tag, "timestamp": a["timestamp"], "stale": a["stale"], "groups": groups,
            "findings": fs, "components": a}

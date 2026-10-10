"""
Component states for the 3D twin, and the inspector text behind each one.

States, from evidence only:
  green   no monitored abnormality within the available evidence
  yellow  a concerning deviation, trend or unresolved uncertainty
  red     a defined critical condition or serious model discrepancy
  grey    data unavailable or stale, or the component cannot be assessed
  sensor  instrument fault, transformer unaffected (drawn as a violet outline)

An unmeasured component is never green by default. Every field of the
inspector is generated from the numbers at the cursor, so the text and the
colour cannot disagree.

assess() takes an evidence bundle. from_twin() builds it from the analytics at
a cursor; dtwin/twin/simulate.py builds one from a simulated operating point and
passes it through the same rules.
"""

from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml

from ..physics.ageing import ageing_factor
from ..physics.thermal import hotspot_rise_ultimate

CONFIG = Path(__file__).resolve().parents[2] / "config" / "components.yaml"
PHASES = ("R", "Y", "B")
BAD = ("dead", "stuck", "suspect", "absent")


@lru_cache(maxsize=1)
def config():
    return yaml.safe_load(CONFIG.read_text())


def _f(x, nd=1):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), nd)


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #
def from_twin(tw, i, fresh=None):
    r = tw.rows.iloc[i]
    df = tw.df.iloc[i]
    np_ = tw.np_
    st = tw.state(i)
    return {
        "np": np_, "timestamp": st["timestamp"],
        "oil_c": float(df.oil_temp) if np.isfinite(df.oil_temp) else None,
        "ambient_c": float(df.ambient_temp) if np.isfinite(df.ambient_temp) else None,
        "pred_oil_c": float(r.pred_oil_c),
        "currents": {p: float(df[f"I{p}"]) if np.isfinite(df[f"I{p}"]) else None for p in PHASES},
        "voltages": {p: float(df[f"V{p}"]) if np.isfinite(df[f"V{p}"]) else None for p in PHASES},
        "channels": {c: r[f"ch_{c}"] for c in ("IR", "IY", "IB", "VR", "VY", "VB", "oil_temp", "ambient_temp")},
        "suspect": [c for c in (r.suspect_channels or "").split(",") if c],
        "slope": float(r.slope) if np.isfinite(r.slope) else None,
        "intercept": float(r.intercept) if np.isfinite(r.intercept) else None,
        "thermal_status": r.thermal_status, "elec_status": r.elec_status,
        "offset_channel": r.offset_channel or "",
        "residual_k": float(r.residual_k) if np.isfinite(r.residual_k) else None,
        "phase_dev_pct": float(r.phase_dev_max_pct),
        "alerts_live": st["alerts_live"],
        "stale": bool(fresh and fresh.get("stale")), "stale_reason": (fresh or {}).get("reason"),
        "simulated": False,
        "connection": connection_evidence(tw, i),
        "oc_test": config().get("oc_test"),
    }


def connection_evidence(tw, i, hours=24.0):
    """Apparent series resistance of each phase's LV path: the slope of its
    phase voltage against its own current over the last `hours`. A
    high-resistance joint shows as one phase whose voltage falls faster with
    load than the others'. Uses trusted samples only."""
    t = tw.rows.index[i]
    w = tw.df.loc[t - __import__("pandas").Timedelta(hours=hours):t]
    rw = tw.rows.loc[w.index]
    out = {}
    for ph in PHASES:
        ok = rw[f"ch_I{ph}"].isin(["ok", "step"]) & rw[f"ch_V{ph}"].isin(["ok", "step"])
        x, y = w[f"I{ph}"][ok].to_numpy(float), w[f"V{ph}"][ok].to_numpy(float)
        m = np.isfinite(x) & np.isfinite(y)
        x, y = x[m], y[m]
        if len(x) < 12:
            out[ph] = {"r_ohm": None, "n": int(len(x)), "spread_a": None}
            continue
        spread = float(np.percentile(x, 90) - np.percentile(x, 10))
        slope = float(np.cov(x, y)[0, 1] / np.var(x, ddof=1)) if np.var(x) > 0 else float("nan")
        out[ph] = {"r_ohm": -slope, "n": int(len(x)), "spread_a": spread}
    return {"hours": hours, "phases": out}


# --------------------------------------------------------------------------- #
# Assessors: each returns the full inspector record
# --------------------------------------------------------------------------- #
def _rec(state, reason, observed=(), expected=(), rule=None, deviation=None, hypotheses=(),
         verification=(), missing=(), limits=()):
    return {"state": state, "reason": reason, "observed": list(observed), "expected": list(expected),
            "rule": rule, "deviation": deviation, "hypotheses": list(hypotheses),
            "verification": list(verification), "missing": list(missing), "limits": list(limits)}


def _obs(label, value, unit="", nd=1):
    return {"label": label, "value": _f(value, nd) if isinstance(value, (int, float)) else value, "unit": unit}


def a_unmonitored(c, ev):
    return _rec("grey", "Not monitored: no sensor gives evidence about this component.",
                missing=c.get("missing", []),
                verification=["Visual inspection on the next site visit."],
                limits=["The twin cannot assess what is not measured; grey here is not a fault."])


def a_winding(c, ev):
    np_, W = ev["np"], config()["winding"]
    ph, side = c["phase"], c["side"]
    ch = f"I{ph}"
    i_a = ev["currents"].get(ph)
    if ev["channels"].get(ch) in BAD or i_a is None:
        return _rec("grey", f"{ch} current channel is {ev['channels'].get(ch)}: this phase cannot be assessed.",
                    observed=[_obs(f"{ch} channel", ev["channels"].get(ch))],
                    missing=[f"a trusted {ch} reading"], verification=[f"Clamp-meter the {ph}-phase current."])
    probe_ok = ev["oil_c"] is not None and ev["channels"].get("oil_temp") not in BAD \
        and "oil_temp" not in ev["suspect"]
    oil = ev["oil_c"] if probe_ok else ev["pred_oil_c"]
    k = i_a / np_.rated_current_a
    grad = hotspot_rise_ultimate(k, np_)
    hs = oil + grad
    faa = float(ageing_factor(hs, np_))
    two_m = 2 * np_.m_exponent
    rule = {"equation": "theta_hs = theta_oil + dTheta_hs,R x K^(2m)",
            "substituted": f"{oil:.1f} + {np_.hotspot_rise_rated_k:g} x {k:.2f}^{two_m:g} = {hs:.1f} C",
            "result": f"ageing factor F_AA = {faa:.2f}"}
    worst = max(PHASES, key=lambda p: abs((ev["currents"].get(p) or 0) - np.nanmean(
        [v for v in ev["currents"].values() if v is not None])))
    unb_here = ev["elec_status"] == "asset_unbalance" and worst == ph
    if hs >= W["hotspot_alarm_c"] or k >= W["load_alarm_pu"]:
        state = "red"
        why = (f"{ph}-phase current {i_a:.1f} A is {100 * k:.0f} % of rated" if k >= W["load_alarm_pu"]
               else f"hot-spot {hs:.1f} C is above the {W['hotspot_alarm_c']} C limit")
    elif hs >= W["hotspot_warn_c"] or k >= W["load_warn_pu"] or unb_here:
        state = "yellow"
        why = ("this phase carries the unbalance" if unb_here else
               f"hot-spot {hs:.1f} C: insulation ageing at {faa:.2f}x its rated rate" if hs >= W["hotspot_warn_c"]
               else f"{ph}-phase load {100 * k:.0f} % is close to rating")
    else:
        state, why = "green", f"hot-spot {hs:.1f} C, load {100 * k:.0f} % of rated"
    inferred = side == "HV"
    obs = [_obs(f"{ch} (LV phase current)", i_a, "A"), _obs("Load on this phase", 100 * k, "%", 0),
           _obs("Top oil" + ("" if probe_ok else " (modelled: probe untrusted)"), oil, "°C"),
           _obs("Hot-spot (computed)", hs, "°C"), _obs("Ageing factor", faa, "pu", 2)]
    if inferred:
        obs.insert(1, _obs("HV current (inferred, I_LV / a)", i_a * np_.lv_v / (np_.hv_kv * 1000), "A", 2))
    hyps = []
    if state != "green":
        if unb_here:
            hyps.append("Single-phase loads concentrated on this phase (hypothesis).")
        if k >= W["load_warn_pu"]:
            hyps.append("Load above the planned share for this phase (hypothesis).")
        if hs >= W["hotspot_warn_c"]:
            hyps.append("Hot ambient with the unit already near its thermal limit (hypothesis).")
    return _rec(state, f"{'Inferred from the LV current via the turns ratio. ' if inferred else ''}"
                       f"{why[0].upper() + why[1:]}.",
                observed=obs,
                expected=[_obs("Hot-spot at rated load, 30 C ambient", 110, "°C"),
                          _obs("Warning / alarm", f"{W['hotspot_warn_c']} / {W['hotspot_alarm_c']} C hot-spot, "
                                                  f"{100 * W['load_warn_pu']:.0f} / {100 * W['load_alarm_pu']:.0f} % load")],
                rule=rule,
                deviation=f"{hs - W['hotspot_warn_c']:+.1f} K against the {W['hotspot_warn_c']} C warning level",
                hypotheses=hyps,
                verification=(["Thermal camera on the LV terminals of this phase.",
                               f"Clamp-meter {ph}-phase current to confirm the logger."] if state != "green" else
                              ["None needed."]),
                missing=["Direct winding temperature (fibre-optic probe).",
                         "Heat-run test gradient for this unit."] + (["Any HV-side measurement."] if inferred else []),
                limits=["Steady-state gradient: the 7 min winding time constant is ignored at 15 min sampling.",
                        "C57.91 gives one gradient; HV and LV windings are taken as equal."])


def a_core(c, ev):
    """In service the measured current is LOAD current, so the no-load
    current, the one direct electrical evidence of core condition, cannot be
    observed. The core stays grey unless open-circuit test data are supplied."""
    from ..physics import circuit as Cc
    np_, th = ev["np"], c["thresholds"]
    vs = {p: v for p, v in ev["voltages"].items() if v is not None and ev["channels"].get(f"V{p}") not in BAD}
    obs = [_obs(f"V{p}", v, "V") for p, v in vs.items()]
    if vs:
        pu = max(vs.values()) / np_.voltage_nominal_ln_v
        obs.append(_obs("Highest phase voltage", pu, "pu", 3))
    oc = ev.get("oc_test")
    if not oc:
        note = (f" Highest phase voltage is {pu:.3f} pu" + (", above the overfluxing margin: worth checking the tap."
                                                             if pu > th["overflux_warn_pu"] else ".")) if vs else ""
        return _rec("grey", "Not assessable in service: the measured current is load current, so the no-load "
                            "current that reflects core condition cannot be observed." + note,
                    observed=obs,
                    expected=[_obs("Needed", "an open-circuit test: rated voltage, secondary open")],
                    rule={"equation": "core condition <- I0 and P0 at rated voltage (open-circuit test)",
                          "substituted": "no open-circuit test data configured",
                          "result": "not assessed"},
                    missing=["Open-circuit test (V, I0, P0) for this unit"] + c.get("missing", []),
                    verification=["Run an open-circuit test at the next outage; enter it as oc_test in "
                                  "config/components.yaml."],
                    limits=["Overfluxing from high voltage is shown as an observation only."])
    p = Cc.from_nameplate(np_)
    test = Cc.open_circuit_test(oc["v_line"], oc["i_line"], oc["p_w"])
    side_a = p.a if str(oc.get("side", "LV")).upper() == "LV" else 1.0
    rc_hv, xm_hv = test["Rc_ohm"] * side_a ** 2, test["Xm_ohm"] * side_a ** 2
    dev = max(abs(rc_hv / p.Rc - 1), abs(xm_hv / p.Xm - 1))
    state = "green" if dev <= 0.2 else "yellow"
    return _rec(state, f"Open-circuit test: Rc and Xm are within {100 * dev:.0f} % of the design values.",
                observed=obs + [_obs("Rc from test (HV)", rc_hv / 1000, "kohm"), _obs("Xm from test (HV)", xm_hv / 1000, "kohm")],
                expected=[_obs("Rc design (HV)", p.Rc / 1000, "kohm"), _obs("Xm design (HV)", p.Xm / 1000, "kohm")],
                rule={"equation": "Rc = V / Ic, Xm = V / Im, Ic = P0 / V, Im = sqrt(I0^2 - Ic^2)",
                      "substituted": f"Ic {test['Ic_a']:.3f} A, Im {test['Im_a']:.3f} A on the test side",
                      "result": f"largest deviation {100 * dev:.0f} % (20 % allowed)"},
                deviation=f"{100 * dev:.0f} % from design",
                hypotheses=["Lamination insulation damage or a core-joint problem (hypothesis)."] if state != "green" else [],
                verification=["Repeat the test at rated voltage; compare with the factory test."] if state != "green" else ["None needed."],
                limits=["The design Rc and Xm are illustrative unless factory test data replace them."])


def a_connection(c, ev):
    cfg = config()["connection"]
    ph = c["phase"]
    conn = ev.get("connection")
    if not conn:
        return _rec("grey", "Not modelled in a simulation: connection condition needs measured voltage and current.",
                    missing=c.get("missing", []))
    me = conn["phases"].get(ph, {})
    others = [conn["phases"][q]["r_ohm"] for q in PHASES if q != ph and conn["phases"][q]["r_ohm"] is not None]
    if me.get("r_ohm") is None or not others:
        return _rec("grey", f"Too few trusted {ph}-phase samples in the last {conn['hours']:g} h to assess the connection.",
                    missing=c.get("missing", []))
    if me["spread_a"] < cfg["min_current_spread_a"]:
        return _rec("grey", f"The {ph}-phase current varied by only {me['spread_a']:.1f} A in the last {conn['hours']:g} h: "
                            "too little to estimate the connection resistance.", missing=c.get("missing", []))
    ref = float(np.median(others))
    ratio = me["r_ohm"] / ref if ref > 0 else float("inf")
    state = "yellow" if ratio > cfg["ratio_warn"] else "green"
    r_m, ref_m = 1000 * me["r_ohm"], 1000 * ref
    why = (f"the {ph}-phase voltage falls {ratio:.1f}x faster with load than the other phases'"
           if state == "yellow" else f"the {ph}-phase voltage falls with load like the other phases'")
    return _rec(state, why[0].upper() + why[1:] + ".",
                observed=[_obs(f"Apparent series resistance, {ph}", r_m, "mohm"),
                          _obs("Median of the other phases", ref_m, "mohm"),
                          _obs("Samples, last 24 h", me["n"], "", 0),
                          _obs(f"{ph}-phase current spread (p10-p90)", me["spread_a"], "A")],
                expected=[_obs("Ratio to the other phases", f"<= {cfg['ratio_warn']}")],
                rule={"equation": "r = -dV_ph / dI_ph (least squares over 24 h of trusted samples)",
                      "substituted": f"r_{ph} = {r_m:.2f} mohm, others {ref_m:.2f} mohm, ratio {ratio:.2f}",
                      "result": "a joint adding resistance in one phase only" if state == "yellow" else "consistent"},
                deviation=f"x{ratio:.2f} the other phases",
                hypotheses=([f"High-resistance joint at the {ph}-phase LV terminal or cable lug (hypothesis).",
                             f"Undersized or damaged {ph}-phase cable (hypothesis)."] if state == "yellow" else []),
                verification=([f"Thermal image of the {ph}-phase LV bushing and lug under load.",
                               "Measure joint resistance (micro-ohmmeter) at the next outage."]
                              if state == "yellow" else ["None needed."]),
                missing=c.get("missing", []),
                limits=["The slope includes the transformer's own impedance and the cable; only the difference "
                        "between phases is used.", cfg["source"]])


def a_oil(c, ev):
    th = c["thresholds"]
    if ev["oil_c"] is None or ev["channels"].get("oil_temp") in BAD or "oil_temp" in ev["suspect"]:
        return _rec("grey", "The oil probe is untrusted: the oil temperature cannot be assessed from measurement.",
                    observed=[_obs("Oil probe", ev["channels"].get("oil_temp"))],
                    expected=[_obs("Twin prediction", ev["pred_oil_c"], "°C")],
                    missing=["A trusted top-oil reading"] + c.get("missing", []))
    t, pred, res = ev["oil_c"], ev["pred_oil_c"], ev["residual_k"]
    if t >= th["top_oil_alarm_c"]:
        state, why = "red", f"top oil {t:.1f} C is above the {th['top_oil_alarm_c']} C limit"
    elif t >= th["top_oil_warn_c"] or ev["thermal_status"] == "thermal_mismatch":
        state = "yellow"
        why = (f"top oil {t:.1f} C is near the limit" if t >= th["top_oil_warn_c"]
               else "measured rise is lower than the nameplate predicts")
    else:
        state, why = "green", f"top oil {t:.1f} C, within limits"
    return _rec(state, why[0].upper() + why[1:] + ".",
                observed=[_obs("Top oil (measured)", t, "°C"), _obs("Ambient", ev["ambient_c"], "°C"),
                          _obs("Residual, measured - twin", res, "K", 2)],
                expected=[_obs("Twin prediction (C57.91, healthy cooling)", pred, "°C"),
                          _obs("Warning / limit", f"{th['top_oil_warn_c']} / {th['top_oil_alarm_c']} C")],
                rule={"equation": "theta_oil = theta_amb + dTheta_oil,R x ((K^2 R + 1)/(R + 1))^n, lagged by tau = Rth Cth",
                      "substituted": f"twin {pred:.1f} C vs measured {t:.1f} C",
                      "result": f"residual {res:+.2f} K" if res is not None else ""},
                deviation=f"{t - th['top_oil_warn_c']:+.1f} K against the warning level",
                hypotheses=["Sustained high load in hot weather (hypothesis)."] if state != "green" else [],
                verification=["Read the oil gauge and sample the oil (DGA, moisture)."] if state != "green" else ["None needed."],
                missing=c.get("missing", []),
                limits=[th["source"]])


def a_cooling(c, ev):
    th = c["thresholds"]
    if ev["thermal_status"] in ("unavailable", "insufficient") or ev["slope"] is None:
        return _rec("grey", "The rise fit is not available (missing temperatures or too little history).",
                    observed=[_obs("Thermal diagnosis", ev["thermal_status"])], missing=c.get("missing", []))
    s, b = ev["slope"], ev["intercept"]
    if ev["thermal_status"] == "asset_cooling":
        state, why = "red", f"oil rise is {100 * (s - 1):.0f} % above what load and ambient explain"
    elif s > th["slope_warn"]:
        state, why = "yellow", f"rise-fit slope {s:.3f} is above the healthy band but under the alarm"
    else:
        state, why = "green", f"rise-fit slope {s:.3f}: cooling as designed"
    note = " A temperature instrument offset is active; the slope is insensitive to a constant offset." \
        if ev["thermal_status"] == "sensor_temp_offset" else ""
    return _rec(state, why[0].upper() + why[1:] + "." + note,
                observed=[_obs("Rise-fit slope", s, "", 3), _obs("Intercept", b, "K", 2),
                          _obs("Top oil", ev["oil_c"], "°C"), _obs("Twin prediction", ev["pred_oil_c"], "°C")],
                expected=[_obs("Slope, healthy cooling", "1.00 (healthy p95 1.025)"),
                          _obs("Alarm", f"slope > {th['slope_alarm']} for 3 consecutive hourly fits")],
                rule={"equation": "measured rise = a x predicted rise + b + c t   (24 h Huber fit)",
                      "substituted": f"a = {s:.3f}, b = {b:+.2f} K" if b is not None else f"a = {s:.3f}",
                      "result": "a > 1.06: the rise scales with load, so the transformer changed, not the instrument"
                      if state == "red" else f"a {'>' if s > th['slope_warn'] else '<='} {th['slope_warn']}"},
                deviation=f"{100 * (s - 1):+.1f} % rise against design",
                hypotheses=(["Radiator fins fouled with dust or debris (hypothesis).",
                             "Low oil level reducing circulation (hypothesis).",
                             "A radiator valve closed or blocked (hypothesis)."] if state != "green" else []),
                verification=(["Inspect and clean the fins.", "Check that the radiator valves are open.",
                               "Read the conservator oil gauge.",
                               "Thermal image: top-to-bottom fin gradient should be similar across radiators."]
                              if state != "green" else ["None needed."]),
                missing=c.get("missing", []),
                limits=["The fit needs 24 h of history; a missed temperature step can distort it.", th["source"]])


def a_conservator(c, ev):
    if ev["thermal_status"] == "asset_cooling":
        return _rec("yellow", "Oil level is not measured, and the cooling alarm is active: low oil is one possible cause.",
                    hypotheses=["Oil loss through a leak lowering the level (hypothesis)."],
                    verification=["Read the oil gauge.", "Look for oil stains at gaskets and the drain valve."],
                    missing=c.get("missing", []))
    return a_unmonitored(c, ev)


def a_oil_probe(c, ev):
    chs = ev["channels"].get("oil_temp")
    off = ev["thermal_status"] == "sensor_temp_offset" and "oil_temp" in ev["offset_channel"]
    if chs in BAD or off:
        both = ev["offset_channel"] == "oil_temp|ambient_temp"
        why = (f"probe reading is {chs}" if chs in BAD else
               f"every reading is shifted by {ev['intercept']:+.1f} K at every load"
               + (": oil or ambient probe, they are thermally identical" if both else ""))
        return _rec("sensor", f"Instrument fault, transformer unaffected: {why}.",
                    observed=[_obs("Oil probe channel", chs), _obs("Intercept", ev["intercept"], "K", 2),
                              _obs("Slope", ev["slope"], "", 3)],
                    expected=[_obs("Intercept, healthy", "within +/- 2 K")],
                    rule={"equation": "measured rise = a x predicted rise + b",
                          "substituted": f"a = {ev['slope']:.3f}, b = {ev['intercept']:+.2f} K" if ev["slope"] else "",
                          "result": "a ~ 1 and |b| > 2 K: a constant shift, so the instrument changed"},
                    deviation=f"{ev['intercept']:+.2f} K offset" if ev["intercept"] is not None else None,
                    hypotheses=["Probe calibration drift (hypothesis).", "Probe not fully seated in the pocket (hypothesis)."]
                    + (["Ambient probe reading low instead (hypothesis): the two cannot be told apart."] if both else []),
                    verification=["Check the probe against a reference thermometer in the pocket.",
                                  "Do not dispatch a crew for a transformer fault."],
                    limits=["An oil-high and an ambient-low offset look identical to the thermal fit."])
    if ev["oil_c"] is None:
        return _rec("grey", "No oil temperature reading.", missing=["oil_temp channel"])
    return _rec("green", "Probe agrees with the thermal twin.",
                observed=[_obs("Measured", ev["oil_c"], "°C"), _obs("Twin", ev["pred_oil_c"], "°C")],
                rule={"equation": "|intercept| <= 2 K", "substituted": f"b = {ev['intercept']:+.2f} K" if ev["intercept"] is not None else "",
                      "result": "consistent"})


def a_monitor(c, ev):
    bad = {ch: s for ch, s in ev["channels"].items() if s in ("dead", "stuck", "suspect")}
    off = ev["thermal_status"] == "sensor_temp_offset"
    obs = [_obs(ch, s) for ch, s in ev["channels"].items()]
    if bad or off:
        what = ", ".join(f"{ch} {s}" for ch, s in bad.items()) + \
            (("; " if bad else "") + f"temperature offset ({ev['offset_channel'].replace('|', ' or ')})" if off else "")
        return _rec("sensor", f"Instrument fault, transformer unaffected: {what}.", observed=obs,
                    verification=["Check wiring and the logger input for the named channel.",
                                  "Do not dispatch a crew for a transformer fault."],
                    limits=["Failed current channels are rebuilt from the other phases for the thermal model."])
    return _rec("green", "All channels trusted: each agrees with the other phases and with the thermal twin.",
                observed=obs)


ASSESS = {"unmonitored": a_unmonitored, "winding": a_winding, "core": a_core, "oil": a_oil,
          "connection": a_connection,
          "cooling": a_cooling, "conservator": a_conservator, "oil_probe": a_oil_probe, "monitor": a_monitor}

FAULT_COMPONENT = {"cooling": "radiators", "mismatch": "oil", "temp_offset": "oil_pocket",
                   "channel_dead": "monitor_box", "channel_stuck": "monitor_box", "channel_suspect": "monitor_box"}


def assess(ev):
    """Every component's state and inspector record for one evidence bundle."""
    out = []
    for c in config()["components"]:
        rec = ASSESS[c["assess"]](c, ev)
        if ev.get("stale") and c["assess"] != "unmonitored":    # every evidence-based part states the staleness
            rec = {**rec, "state": "grey", "reason": f"No recent data: {ev.get('stale_reason')}. "
                                                       f"Last known: {rec['reason']}"}
        family = "sensor" if rec["state"] == "sensor" else "asset" if rec["state"] in ("red", "yellow") else None
        out.append({"id": c["id"], "name": c["name"], "group": c["group"], **rec,
                    "pulse": rec["state"] == "red" and not ev.get("stale"),
                    "family": family, "hotspot_c": None})
        if c["assess"] == "winding" and rec["observed"]:
            hs = next((o["value"] for o in rec["observed"] if o["label"].startswith("Hot-spot")), None)
            out[-1]["hotspot_c"] = hs
    newest = None
    if ev["alerts_live"] and not ev.get("stale"):
        a = max(ev["alerts_live"], key=lambda a: a["start"])
        cid = FAULT_COMPONENT.get(a["kind"])
        if cid is None:                                 # overload or unbalance: the flagged winding
            wind = [x for x in out if x["id"].startswith("lv_winding") and x["hotspot_c"] is not None]
            rank = {"red": 2, "yellow": 1}
            cid = max(wind, key=lambda x: (rank.get(x["state"], 0), x["hotspot_c"]))["id"] if wind else None
        newest = {"component": cid, "what": a["what"], "family": a["family"], "start": a["start"]}
    counts = {s: sum(1 for x in out if x["state"] == s) for s in ("green", "yellow", "red", "grey", "sensor")}
    return {"timestamp": ev["timestamp"], "simulated": ev.get("simulated", False), "stale": ev.get("stale", False),
            "components": out, "newest_fault": newest, "counts": counts,
            "scale": config()["winding"]["colour_scale"]}

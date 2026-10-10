"""
Simulate workspace: an isolated copy of the observed baseline, edited and
stepped forward. A simulation never writes to observed data: it reads a
snapshot at the cursor and returns new objects.

Physics:
  edited inputs -> per-phase equivalent circuit (dtwin/physics/circuit.py)
                -> copper and core loss
                -> C57.91 first-order lags, driven by losses:
                     oil rise_ult = cf x rise_R x (P_total / P_total,R)^n,  tau_oil = Rth x Cth
                     gradient_ult = grad_R x (P_cu / P_cu,R)^m,            tau_winding
                stepped every 10 min from the observed thermal state.
  With no edits the result equals the C57.91 K-based model (check_simulate.py).

Inputs that have no test data behind them (impedance, Rc, Xm, losses, power
factor) are ILLUSTRATIVE and editable; results are what-if estimates, not
predictions for this specific unit.
"""

import copy
import math

import numpy as np

from ..physics import circuit as C
from ..physics.ageing import ageing_factor
from . import components as TC

ASSUMED_PF = 0.9
INPUTS = {
    "input_voltage_pu": {"label": "Input voltage", "unit": "pu of rated", "min": 0.85, "max": 1.15, "step": 0.01},
    "load_pct": {"label": "Load", "unit": "% of rated", "min": 0, "max": 160, "step": 1},
    "ambient_c": {"label": "Ambient temperature", "unit": "°C", "min": 0, "max": 55, "step": 0.5},
    "winding_r_mult": {"label": "Winding resistance", "unit": "x design", "min": 0.8, "max": 2.0, "step": 0.05},
    "leakage_x_mult": {"label": "Leakage impedance", "unit": "x design", "min": 0.5, "max": 2.0, "step": 0.05},
    "rc_mult": {"label": "Core-loss resistance Rc", "unit": "x design", "min": 0.3, "max": 2.0, "step": 0.05},
    "xm_mult": {"label": "Magnetising reactance Xm", "unit": "x design", "min": 0.3, "max": 2.0, "step": 0.05},
    "cooling_effectiveness": {"label": "Cooling effectiveness", "unit": "x design", "min": 0.5, "max": 1.2, "step": 0.01},
    "pf": {"label": "Load power factor (lagging)", "unit": "", "min": 0.6, "max": 1.0, "step": 0.01},
}
UNSUPPORTED = [
    {"scenario": "Oil leakage", "needs": ["oil level (conservator gauge or level switch)", "oil temperature at several heights"],
     "cannot": "The prototype has no oil-level channel. A leak shows only indirectly, as reduced cooling, and the "
               "rate of oil loss cannot be predicted."},
    {"scenario": "Vibration", "needs": ["tank-mounted accelerometer", "core and winding resonance from a factory test"],
     "cannot": "No vibration channel exists, so loosening of the core or windings cannot be simulated."},
    {"scenario": "Internal pressure", "needs": ["pressure sensor or pressure-relief device status", "dissolved gas analysis"],
     "cannot": "Gas generation from an internal fault needs DGA and a fault model the prototype does not have."},
]


def baseline(tw, i):
    """A deep, read-only snapshot of the observed state at sample i."""
    r, d = tw.rows.iloc[i], tw.df.iloc[i]
    np_ = copy.deepcopy(tw.np_)
    p = C.from_nameplate(np_)
    cur = [float(d[c]) for c in ("IR", "IY", "IB") if np.isfinite(d[c])]
    i_mean = float(np.mean(cur)) if cur else float(r.K_oil) * np_.rated_current_a
    volts = [float(d[c]) for c in ("VR", "VY", "VB") if np.isfinite(d[c])]
    v_ln = float(np.mean(volts)) if volts else np_.voltage_nominal_ln_v
    oil = float(d.oil_temp) if np.isfinite(d.oil_temp) else float(r.pred_oil_c)
    amb = float(d.ambient_temp) if np.isfinite(d.ambient_temp) else 30.0
    cf = float(r.slope) if r.thermal_status == "asset_cooling" and np.isfinite(r.slope) else 1.0
    phase_ratio = {ph: (float(d[f"I{ph}"]) / i_mean if np.isfinite(d[f"I{ph}"]) and i_mean > 0 else 1.0)
                   for ph in ("R", "Y", "B")}
    # the primary voltage that gives the measured LV voltage at this load
    v2_rated_ln = p.v2_ph
    return {"np": np_, "circuit": p, "timestamp": tw.rows.index[i].isoformat(),
            "inputs": {"input_voltage_pu": round(min(max(v_ln / v2_rated_ln, 0.85), 1.15), 3),
                       "load_pct": round(100 * i_mean / np_.rated_current_a, 1), "ambient_c": round(amb, 1),
                       "winding_r_mult": 1.0, "leakage_x_mult": 1.0, "rc_mult": 1.0, "xm_mult": 1.0,
                       "cooling_effectiveness": round(1.0 / cf, 3), "pf": ASSUMED_PF},
            "oil_c": oil, "hotspot_c": float(tw.rows.hotspot_c.iloc[i]), "phase_ratio": phase_ratio,
            "thermal_status": r.thermal_status, "slope": float(r.slope) if np.isfinite(r.slope) else None}


def _operate(base, inp):
    """Circuit operating point for a set of inputs."""
    p0 = base["circuit"]
    p = C.scaled(p0, r=inp["winding_r_mult"], x=inp["leakage_x_mult"], rc=inp["rc_mult"], xm=inp["xm_mult"])
    op = C.solve_from_primary(p, v1_ph=p0.v1_ph * inp["input_voltage_pu"], load_pu=inp["load_pct"] / 100.0,
                              pf=inp["pf"], lagging=True)
    return p, op


def _trajectory(base, inp, horizon_h, step_min):
    np_ = base["np"]
    pc0, pk0 = C.rated_losses(base["circuit"])
    p, op = _operate(base, inp)
    cf = 1.0 / max(inp["cooling_effectiveness"], 1e-6)
    loss_ratio = (op.p_cu_w + op.p_core_w) / (pc0 + pk0)
    worst = max(base["phase_ratio"].values())
    cu_ratio_worst = (op.p_cu_w / pc0) * worst ** 2
    rise_ult = cf * np_.top_oil_rise_rated_k * loss_ratio ** np_.n_exponent
    grad_ult = np_.hotspot_rise_rated_k * max(cu_ratio_worst, 0.0) ** np_.m_exponent
    amb = inp["ambient_c"]
    rise = base["oil_c"] - base["inputs"]["ambient_c"]          # the observed state at the cursor
    grad = base["hotspot_c"] - base["oil_c"]
    a_o = 1 - math.exp(-step_min / (np_.tau_oil_h * 60))
    a_w = 1 - math.exp(-step_min / np_.tau_winding_min)
    t, oil, hs, faa, aged = [], [], [], [], []
    acc = 0.0
    for k in range(int(horizon_h * 60 / step_min) + 1):
        if k:
            rise += a_o * (rise_ult - rise)
            grad += a_w * (grad_ult - grad)
        o, h = amb + rise, amb + rise + grad
        f = float(ageing_factor(h, np_))
        if k:
            acc += f * step_min / 60
        t.append(round(k * step_min / 60, 3))
        oil.append(round(o, 2))
        hs.append(round(h, 2))
        faa.append(round(f, 4))
        aged.append(round(acc, 4))
    steady_oil = amb + rise_ult
    steady_hs = steady_oil + grad_ult
    s = op.summary()
    lim = np_.reference_hotspot_c
    t_lim = next((tt for tt, x in zip(t, hs) if x >= lim), None)
    t_trip = next((tt for tt, x in zip(t, oil) if x >= np_.trip_oil_temp_c), None)
    faa_ss = float(ageing_factor(steady_hs, np_))
    return {"series": {"hours": t, "top_oil_c": oil, "hotspot_c": hs, "faa": faa, "aged_h": aged},
            "point": {**{k: round(v, 4) if isinstance(v, float) else v for k, v in s.items()},
                      "loss_ratio": round(loss_ratio, 4), "cooling_factor": round(cf, 4),
                      "steady_top_oil_c": round(steady_oil, 2), "steady_hotspot_c": round(steady_hs, 2),
                      "steady_faa": round(faa_ss, 4),
                      "life_cost_inr_per_day": round(24 * faa_ss * np_.cost_per_hour_of_life_inr, 1),
                      "hours_to_hotspot_limit": t_lim, "hours_to_fixed_trip": t_trip,
                      "aged_h_horizon": aged[-1]},
            "op": op, "circuit": p}


def _safe_load(base, inp):
    """Largest load that keeps the steady hot-spot at the 110 C reference."""
    lo, hi = 0.0, 200.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if _trajectory(base, {**inp, "load_pct": mid}, 0, 60)["point"]["steady_hotspot_c"] <= base["np"].reference_hotspot_c:
            lo = mid
        else:
            hi = mid
    return lo


DELTA_ROWS = [
    ("V2_line_v", "LV output voltage, line", "V", 1), ("regulation_pct", "Voltage regulation", "%", 2),
    ("I2_a", "LV current", "A", 1), ("I0_a", "No-load current I0", "A", 4),
    ("I0_pct_of_rated", "I0, % of rated", "%", 2), ("p_cu_w", "Copper loss", "W", 0),
    ("p_core_w", "Core loss", "W", 0), ("efficiency_pct", "Efficiency", "%", 2),
    ("steady_top_oil_c", "Top oil, steady", "°C", 1), ("steady_hotspot_c", "Hot-spot, steady", "°C", 1),
    ("steady_faa", "Ageing factor, steady", "pu", 2), ("life_cost_inr_per_day", "Life cost", "₹/day", 0),
    ("hours_to_hotspot_limit", "Hours to 110 °C hot-spot", "h", 1),
    ("hours_to_fixed_trip", "Hours to the fixed 60 °C oil trip", "h", 1),
]


def explain(base, inp, b, s):
    """Why each output moved, with the numbers; then actions and limits."""
    bi, out = base["inputs"], []
    bp, sp = b["point"], s["point"]

    def ch(k):
        return abs(inp[k] - bi[k]) > 1e-9

    if ch("load_pct"):
        f = inp["load_pct"] / max(bi["load_pct"], 1e-9)
        out.append(f"Load {bi['load_pct']:.0f} -> {inp['load_pct']:.0f} %: copper loss follows I^2 R, so it scales by "
                   f"{f:.2f}^2 = {f * f:.2f} ({bp['p_cu_w']:.0f} -> {sp['p_cu_w']:.0f} W). The winding gradient follows "
                   f"(copper loss)^m and the oil rise follows (total loss)^n with n = {base['np'].n_exponent:g}.")
    if ch("input_voltage_pu"):
        out.append(f"Input voltage {bi['input_voltage_pu']:.2f} -> {inp['input_voltage_pu']:.2f} pu: core loss follows "
                   f"V^2 / Rc ({bp['p_core_w']:.0f} -> {sp['p_core_w']:.0f} W) and the magnetising current follows "
                   f"V / Xm; the LV voltage follows the ratio less the winding drops.")
    if ch("winding_r_mult"):
        out.append(f"Winding resistance x{inp['winding_r_mult']:.2f}: copper loss is I^2 R, so it rises in proportion "
                   f"at the same current ({bp['p_cu_w']:.0f} -> {sp['p_cu_w']:.0f} W); a rise like this suggests a loose "
                   f"or degraded joint.")
    if ch("leakage_x_mult"):
        out.append(f"Leakage impedance x{inp['leakage_x_mult']:.2f}: the series drop I X grows, so regulation moves "
                   f"{bp['regulation_pct']:.2f} -> {sp['regulation_pct']:.2f} %; reactance dissipates no power, so the "
                   f"losses barely change.")
    if ch("rc_mult"):
        out.append(f"Rc x{inp['rc_mult']:.2f}: core loss is V^2 / Rc ({bp['p_core_w']:.0f} -> {sp['p_core_w']:.0f} W); "
                   f"a lower Rc means a lossier core, for example damaged lamination insulation.")
    if ch("xm_mult"):
        out.append(f"Xm x{inp['xm_mult']:.2f}: the magnetising current is V / Xm, so I0 moves "
                   f"{bp['I0_a'] * 1000:.0f} -> {sp['I0_a'] * 1000:.0f} mA; it is reactive and adds almost no heat.")
    if ch("cooling_effectiveness"):
        out.append(f"Cooling effectiveness x{inp['cooling_effectiveness']:.2f}: the oil rise for the same loss scales by "
                   f"1 / effectiveness = {1 / inp['cooling_effectiveness']:.2f} (thermal resistance Rth rises).")
    if ch("ambient_c"):
        out.append(f"Ambient {bi['ambient_c']:.1f} -> {inp['ambient_c']:.1f} °C: every temperature shifts by the "
                   f"same {inp['ambient_c'] - bi['ambient_c']:+.1f} K; the rises are unchanged.")
    if ch("pf"):
        out.append(f"Power factor {bi['pf']:.2f} -> {inp['pf']:.2f}: at the same current the losses are the same, "
                   f"but regulation moves {bp['regulation_pct']:.2f} -> {sp['regulation_pct']:.2f} %.")
    if not out:
        out.append("No input changed: the simulated trajectory is the baseline held at its present conditions.")

    actions = []
    if sp["steady_hotspot_c"] > base["np"].reference_hotspot_c:
        safe = _safe_load(base, inp)
        actions.append(f"Steady hot-spot {sp['steady_hotspot_c']:.0f} °C exceeds the 110 °C reference: keep the load at or "
                       f"below {safe:.0f} % under these conditions, or restore cooling.")
    if sp["steady_faa"] > 1:
        actions.append(f"Insulation would age {sp['steady_faa']:.1f}x faster than rated (Rs {sp['life_cost_inr_per_day']:.0f} per day).")
    if inp["input_voltage_pu"] > 1.05:
        actions.append("Input voltage above 1.05 pu: check the off-load tap position against the feeder voltage.")
    if abs(sp["regulation_pct"]) > 5:
        actions.append(f"Regulation {sp['regulation_pct']:.1f} %: check the LV voltage at the far end of the feeder.")
    if not actions:
        actions.append("No action indicated: the simulated state stays within the twin's limits.")
    limits = ["Impedance, losses, I0, Rc and Xm are illustrative typical values, not this unit's test data.",
              f"Power factor is assumed {ASSUMED_PF} lagging: the device records no power factor.",
              "Inputs are held constant over the horizon; no daily load or weather cycle is applied.",
              "Rc and Xm are constant: core saturation at high voltage is not modelled.",
              "HV and LV windings share one C57.91 gradient; the worst phase sets the hot-spot."]
    return out, actions, limits


def predicted_components(base, inp, sim):
    """The 3D twin's states for the simulated end state, through the same rules."""
    np_ = base["np"]
    pt = sim["point"]
    oil = sim["series"]["top_oil_c"][-1]
    i_mean = inp["load_pct"] / 100 * np_.rated_current_a
    v2 = pt["V2_phase_v"]
    cf = pt["cooling_factor"]
    ev = {"np": np_, "timestamp": base["timestamp"], "oil_c": oil, "ambient_c": inp["ambient_c"],
          "pred_oil_c": oil, "currents": {ph: i_mean * r for ph, r in base["phase_ratio"].items()},
          "voltages": {ph: v2 for ph in ("R", "Y", "B")},
          "channels": {c: "ok" for c in ("IR", "IY", "IB", "VR", "VY", "VB", "oil_temp", "ambient_temp")},
          "suspect": [], "slope": cf, "intercept": 0.0,
          "thermal_status": "asset_cooling" if cf - 1 > 0.06 else "ok", "elec_status": "ok",
          "offset_channel": "", "residual_k": 0.0, "phase_dev_pct": 0.0, "alerts_live": [],
          "stale": False, "stale_reason": None, "simulated": True}
    out = TC.assess(ev)
    for c in out["components"]:
        c["reason"] = "Simulated: " + c["reason"]
    return out


def simulate(tw, i, inputs=None, horizon_h=24.0, step_min=10.0):
    base = baseline(tw, i)
    inp = {**base["inputs"], **{k: float(v) for k, v in (inputs or {}).items() if k in INPUTS and v is not None}}
    b = _trajectory(base, base["inputs"], horizon_h, step_min)
    s = _trajectory(base, inp, horizon_h, step_min)
    expl, actions, limits = explain(base, inp, b, s)
    delta = [{"key": k, "label": lab, "unit": u, "nd": nd, "baseline": b["point"].get(k), "simulated": s["point"].get(k),
              "delta": (None if b["point"].get(k) is None or s["point"].get(k) is None
                        else round(s["point"][k] - b["point"][k], nd + 1))} for k, lab, u, nd in DELTA_ROWS]
    return {"label": "SIMULATED", "baseline_timestamp": base["timestamp"], "horizon_h": horizon_h,
            "inputs": {"baseline": base["inputs"], "simulated": inp, "spec": INPUTS},
            "illustrative": ["percentage impedance 4.5 %", "copper loss 1.3 % of rating", "core loss = copper loss / R",
                             "no-load current 2 %", f"power factor {ASSUMED_PF}"],
            "thermal_rc": C.thermal_rc(base["np"], base["circuit"]),
            "baseline": {"series": b["series"], "point": b["point"]},
            "simulated": {"series": s["series"], "point": s["point"]},
            "delta": delta, "explanations": expl, "actions": actions, "limits": limits,
            "unsupported": UNSUPPORTED, "components": predicted_components(base, inp, s)}

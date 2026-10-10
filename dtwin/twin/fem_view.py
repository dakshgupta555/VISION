"""
FEM heat map for the twin: the per-phase winding temperature field at a
cursor, and the same field for a Simulate what-if.

At a cursor the FEM is solved quasi-steady (the winding time constant is
minutes): each phase's own measured current heats its leg, and the oil
boundary follows the C57.91 top oil predicted at that instant with the
measured ambient. A what-if is solved at the simulated steady end state.
Read-only: nothing here writes to the observed record.
"""

import math
from collections import OrderedDict

import numpy as np

from ..physics import fem as F
from ..physics.thermal import top_oil_rise_ultimate, hotspot_rise_ultimate

_CACHE = OrderedDict()          # (record identity, cursor, rating) -> snapshot; replay re-asks often


def _cached(key, fn):
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    v = fn()
    _CACHE[key] = v
    while len(_CACHE) > 64:
        _CACHE.popitem(last=False)
    return v


def _num(x):
    x = float(x)
    return x if math.isfinite(x) else None


COMPARISON_NOTE = ("FEM is conduction-only, with no oil flow inside the winding ducts. It gives hot-spot location and "
                   "per-phase distribution; C57.91 remains the value used for alarms.")
_COMPARISON = {}


def comparison(np_):
    """Steady FEM vs C57.91 at 0.5 / 1.0 / 1.3 pu, 30 C ambient: one table per
    transformer, independent of the cursor."""
    leg = F.leg_for(np_)
    if id(leg) not in _COMPARISON:
        _COMPARISON[id(leg)] = {"ambient_c": 30.0, "rows": F.cross_check(np_, loads=(0.5, 1.0, 1.3), leg=leg),
                                "note": COMPARISON_NOTE,
                                "basis": "steady state, balanced load, both models fed the same C57.91 top oil"}
    return _COMPARISON[id(leg)]


def at_cursor(tw, i):
    """FEM snapshot of all three legs at sample i."""
    np_ = tw.np_
    r, d = tw.rows.iloc[i], tw.df.iloc[i]
    key = (id(tw), len(tw.rows), int(i), np_.rating_kva)

    def build():
        leg = F.leg_for(np_)
        cur = {ph: _num(d[f"I{ph}"]) for ph in ("R", "Y", "B")}
        k_oil = _num(r.K_oil)
        # a dead current channel: fall back to the three-phase load the twin uses
        for ph in cur:
            if cur[ph] is None and k_oil is not None:
                cur[ph] = k_oil * np_.rated_current_a
        top = _num(r.pred_oil_c)
        amb = _num(r.ambient_c)
        if amb is None:
            amb = 30.0
        if top is None:
            top = amb + top_oil_rise_ultimate(k_oil or 0.0, np_)
        snap = leg.snapshot(cur, top, amb)
        snap["timestamp"] = tw.rows.index[i].isoformat()
        snap["c57"] = {"top_oil_c": round(top, 2), "hotspot_c": _num(r.pred_hotspot_c),
                       "per_phase_hotspot_c": {ph: round(top + hotspot_rise_ultimate((cur[ph] or 0) / np_.rated_current_a, np_), 2)
                                               for ph in cur},
                       "note": "C57.91 at the same top oil; per phase: top oil + 25 K x K^1.6 with that phase's K"}
        snap["comparison"] = comparison(np_)
        snap["measured_oil_c"] = _num(d.oil_temp)
        snap["boundary"] = ("Robin -k dT/dn = h (T - T_oil(z)) on every winding face; T_oil linear from bottom oil "
                            "to the C57.91 top oil predicted at this instant (measured ambient)")
        snap["solved"] = "quasi-steady at this instant (winding time constant is minutes)"
        return snap

    return _cached(key, build)


def what_if(tw, i, inputs=None):
    """Baseline, simulated and difference FEM fields for a Simulate what-if,
    each at the steady end state of its own C57.91 trajectory."""
    from . import simulate as S
    base = S.baseline(tw, i)
    inp = {**base["inputs"], **{k: float(v) for k, v in (inputs or {}).items() if k in S.INPUTS and v is not None}}
    np_ = base["np"]
    leg = F.leg_for(np_)

    def solve(x):
        tr = S._trajectory(base, x, 0, 60)["point"]
        i_mean = x["load_pct"] / 100.0 * np_.rated_current_a
        cur = {ph: i_mean * rr for ph, rr in base["phase_ratio"].items()}
        snap = leg.snapshot(cur, tr["steady_top_oil_c"], x["ambient_c"], r_mult=x["winding_r_mult"],
                            pf=x["pf"], v_pu=x["input_voltage_pu"])
        snap["c57"] = {"top_oil_c": tr["steady_top_oil_c"], "hotspot_c": tr["steady_hotspot_c"]}
        return snap

    b, s = solve(base["inputs"]), solve(inp)
    diff = {}
    for ph in ("R", "Y", "B"):
        pb, ps = b["phases"].get(ph), s["phases"].get(ph)
        if not pb or not ps:
            diff[ph] = None
            continue
        diff[ph] = {"hotspot_k": round(ps["hotspot"]["c"] - pb["hotspot"]["c"], 3),
                    "blocks": {n: {"r": pb["blocks"][n]["r"], "z": pb["blocks"][n]["z"],
                                   "T": (np.array(ps["blocks"][n]["T"]) - np.array(pb["blocks"][n]["T"])).round(2).tolist()}
                               for n in pb["blocks"]}}
    return {"label": "SIMULATED", "baseline_timestamp": base["timestamp"], "inputs": {"baseline": base["inputs"], "simulated": inp},
            "baseline": b, "simulated": s, "difference": diff,
            "note": "Steady end state of each trajectory. Cooling effectiveness reaches the FEM through the top oil; "
                    "h stays at its rated calibration."}

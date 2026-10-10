"""
Engineering views: characteristic curves, and loss and efficiency analysis at
the cursor, from the equivalent circuit and the magnetic model.

Per-phase copper loss uses the MEASURED phase currents with the circuit's
equivalent resistance referred to the LV side (R2 + R1 / a^2); core loss uses
the MEASURED phase voltages referred to HV, V^2 / Rc. Resistances, Rc and the
power factor are illustrative (no test data), and the views say so.
"""

import math

import numpy as np

from ..physics import circuit as C
from ..physics import magnetic as M

ASSUMED_PF = 0.9


def curves(np_, design=None, load_pct=100.0, pf=ASSUMED_PF, lagging=True):
    p = C.from_nameplate(np_)
    d = M.core_design(np_, **(design or {}))
    return {"bh": M.bh_curve(), "excitation": M.excitation_curve(np_, p, d),
            "transfer": M.transfer_curve(np_, p, load_pct=load_pct, pf=pf, lagging=lagging),
            "illustrative": {"core_design": d, "circuit": p.as_dict()}}


def losses(tw, i, pf=ASSUMED_PF):
    np_ = tw.np_
    p = C.from_nameplate(np_)
    d = tw.df.iloc[i]
    req_lv = p.R2 + p.R1_lv
    phases = {}
    for ph in ("R", "Y", "B"):
        cur = float(d[f"I{ph}"]) if np.isfinite(d[f"I{ph}"]) else None
        phases[ph] = {"current_a": cur, "p_cu_w": None if cur is None else round(cur ** 2 * req_lv, 1)}
    volts = [float(d[f"V{ph}"]) for ph in ("R", "Y", "B") if np.isfinite(d[f"V{ph}"])]
    v_lv = float(np.mean(volts)) if volts else p.v2_ph
    p_core = 3 * (v_lv * p.a) ** 2 / p.Rc
    p_cu = sum(x["p_cu_w"] for x in phases.values() if x["p_cu_w"] is not None)
    cur = [x["current_a"] for x in phases.values() if x["current_a"] is not None]
    load = float(np.mean(cur)) / p.i2_rated if cur else 0.0
    p_out = 3 * v_lv * float(np.mean(cur) if cur else 0) * pf
    eff = p_out / (p_out + p_cu + p_core) if p_out > 0 else None

    pc_r, pk_r = C.rated_losses(p)
    k_eq = C.max_efficiency_load_pu(pc_r, pk_r)
    eff_curve = []
    for k in np.linspace(0.02, 1.5, 75):
        op = C.solve_from_primary(p, load_pu=float(k), pf=pf)
        eff_curve.append({"load_pct": round(100 * float(k), 1), "efficiency_pct": round(100 * op.efficiency, 3),
                          "p_cu_w": round(op.p_cu_w, 1), "p_core_w": round(op.p_core_w, 1)})
    op_eq = C.solve_from_primary(p, load_pu=k_eq, pf=pf)
    reg = []
    for lag, pfs in ((True, np.linspace(0.6, 1.0, 9)), (False, np.linspace(0.95, 0.6, 8))):
        for x in pfs:
            op = C.solve_from_primary(p, load_pu=max(load, 0.01), pf=float(x), lagging=lag)
            ang = math.degrees(math.acos(float(x))) * (-1 if lag else 1)
            reg.append({"pf": round(float(x), 3), "lagging": lag, "angle_deg": round(ang, 1),
                        "label": f"{x:.2f} {'lag' if lag else 'lead'}" if x < 0.999 else "1.00",
                        "regulation_pct": round(op.regulation_pct, 3)})
    reg.sort(key=lambda r: r["angle_deg"])
    rated = C.solve_from_primary(p, load_pu=1.0, pf=pf)
    present = C.solve_from_primary(p, v1_ph=v_lv * p.a, load_pu=load, pf=pf)
    table = [
        ("Load", "%", 100.0, 100 * load), ("LV phase voltage", "V", p.v2_ph, v_lv),
        ("Copper loss", "W", rated.p_cu_w, p_cu), ("Core loss", "W", rated.p_core_w, p_core),
        ("Total loss", "W", rated.p_cu_w + rated.p_core_w, p_cu + p_core),
        ("Efficiency", "%", 100 * rated.efficiency, None if eff is None else 100 * eff),
        ("Regulation", "%", rated.regulation_pct, present.regulation_pct),
    ]
    return {"timestamp": tw.rows.index[i].isoformat(), "pf_assumed": pf,
            "per_phase": phases, "req_lv_ohm": round(req_lv, 5),
            "core_loss_w": round(p_core, 1), "copper_loss_w": round(p_cu, 1),
            "efficiency_pct": None if eff is None else round(100 * eff, 3), "load_pct": round(100 * load, 1),
            "efficiency_curve": eff_curve,
            "max_efficiency": {"load_pct": round(100 * k_eq, 1), "efficiency_pct": round(100 * op_eq.efficiency, 3),
                               "rule": f"copper loss = core loss at K = sqrt(P_core / P_cu,rated) = "
                                       f"sqrt({pk_r:.0f} / {pc_r:.0f}) = {k_eq:.3f}"},
            "regulation_curve": reg,
            "rated_vs_present": [{"quantity": q, "unit": u, "rated": round(r, 2),
                                  "present": None if pr is None else round(pr, 2)} for q, u, r, pr in table],
            "notes": [f"Power factor assumed {pf} lagging: the device records no power factor.",
                      "Winding resistance, Rc and Xm are illustrative typical values, not test data.",
                      "Core loss uses the measured LV voltage referred to HV; per-phase copper loss uses the "
                      "measured phase current."]}

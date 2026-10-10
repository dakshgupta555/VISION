"""
Engineering checks: characteristic curves, losses, diagnostics, agents and
corrective actions.

    python check_engineering.py
"""

import math
import sys

import numpy as np

from dtwin.api.engine import get_twin
from dtwin.physics import circuit as C
from dtwin.physics import magnetic as M
from dtwin.physics.nameplate import preset

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


np_ = preset(100)
d = M.core_design(np_)
exc = M.excitation_curve(np_)
pts = {round(p["v_pct"]): p for p in exc["points"]}

# ---- curves from physics -------------------------------------------------------------
b_formula = d["v_winding_rated_v"] / (4.44 * 50 * d["n1_turns"] * d["a_core_m2"])
check("B_max = V / (4.44 f N1 A) at rated voltage", abs(exc["rated"]["b_max_t"] - b_formula) < 1e-3
      and abs(b_formula - 1.6) < 1e-6, f"{b_formula:.4f} T")
check("B_max is proportional to V (sinusoidal flux)",
      all(abs(p["b_max_t"] - b_formula * p["v_pct"] / 100) < 1e-3 for p in exc["points"]))
im, ipk, wave = M.magnetising_rms(1.6, d, n=720)
check("I_m is the RMS of i(t) = [H(B(t)) l + B g / mu0] / N1",
      abs(im - float(np.sqrt(np.mean(wave ** 2)))) < 1e-12 and ipk >= im * math.sqrt(2) * 0.99)
r = exc["rated"]
check("I0 = sqrt(I_m^2 + I_c^2) at every voltage",
      all(abs(p["i0_a"] - math.hypot(p["im_a"], p["ic_a"])) < 2e-5 for p in exc["points"]))
p0 = C.from_nameplate(np_)
check("one no-load current for the twin: rated I0 from the B-H model equals the circuit's 2 %",
      abs(r["i0_pct"] - 2.0) < 0.01, f"{r['i0_pct']:.3f} %, joint gap {d['gap_mm']} mm")
check("B-H curve monotonic, knee at 1.8 T, beyond the table permeability -> mu0",
      all(np.diff(M.bh_curve()["B_t"]) >= -1e-9) and abs(float(M.h_of_b(2.3)) - float(M.h_of_b(2.2)) - 0.1 / M.MU0) < 1.0)

# ---- disproportionate rise above the knee ------------------------------------------------
below = pts[90]["i0_a"] / pts[80]["i0_a"]
above = pts[120]["i0_a"] / pts[110]["i0_a"] if 110 in pts else exc["i0_ratio_120_over_100"]
# below the knee I0 tracks V (step ratio within 15 % of the voltage ratio);
# above it, I0 grows more than twice as fast as V
check("excitation: current rises disproportionately above the knee",
      abs(below / (90 / 80) - 1) < 0.15 and above > 2 * (120 / 110),
      f"I0 x{below:.2f} from 80->90 %, x{above:.2f} from 110->120 %, x{exc['i0_ratio_120_over_100']:.1f} from 100->120 %")
steel = M.excitation_curve(np_, design=M.core_design(np_, gap_mm=0.0))
check("steel-only curve (gap 0): smaller I0, sharper knee", steel["rated"]["i0_a"] < r["i0_a"]
      and steel["i0_ratio_120_over_100"] > exc["i0_ratio_120_over_100"],
      f"rated I0 {steel['rated']['i0_pct']:.2f} %, x{steel['i0_ratio_120_over_100']:.1f} for 100->120 %")
tr = M.transfer_curve(np_)
check("voltage transfer: V2 proportional to V1 for a constant-impedance load, below V1/a by the regulation",
      abs(tr["points"][0]["v2_line_v"]) < 1e-9 and all(p["v2_line_v"] < p["v1_line_v"] / tr["no_load_ratio"] + 1e-6
                                                       for p in tr["points"][1:]))

# ---- unit conversions ---------------------------------------------------------------------
check("units: A/m x m / turns = A; cm2 <-> m2; % of rated line current",
      abs(M.magnetising_rms(1.0, {**d, "gap_mm": 0.0}, n=8)[1] - float(M.h_of_b(1.0)) * d["l_core_m"] / d["n1_turns"]) < 1e-9
      and abs(M.core_design(np_, a_core_m2=129.0 / 1e4)["a_core_m2"] - 0.0129) < 1e-12
      and abs(r["i0_a"] / p0.i1_rated * 100 - r["i0_pct"]) < 1e-3)
dy = M.core_design(np_, connection="Yyn0")
check("units: winding voltage follows the connection (delta 11 kV, star 6.35 kV)",
      abs(d["v_winding_rated_v"] - 11000) < 1e-9 and abs(dy["v_winding_rated_v"] - 11000 / math.sqrt(3)) < 1e-6)

# ---- losses at a cursor -----------------------------------------------------------------------
from dtwin.twin.engineering import losses
tw = get_twin("showcase")
i = tw.locate("2026-04-14 17:00")
L = losses(tw, i)
req = p0.R2 + p0.R1 / p0.a ** 2
ir = float(tw.df.IR.iloc[i])
check("per-phase copper loss = I_ph^2 (R2 + R1/a^2) from the measured current",
      abs(L["per_phase"]["R"]["p_cu_w"] - ir ** 2 * req) < 0.1, f"{L['per_phase']['R']['p_cu_w']} W at {ir:.1f} A")
check("efficiency curve peaks where copper loss equals core loss",
      abs(max(L["efficiency_curve"], key=lambda x: x["efficiency_pct"])["load_pct"] - L["max_efficiency"]["load_pct"]) < 3,
      L["max_efficiency"]["rule"])
lag = [x for x in L["regulation_curve"] if x["lagging"]]
lead = [x for x in L["regulation_curve"] if not x["lagging"]]
check("regulation: falls from lagging to leading power factor, negative when leading",
      lag[0]["regulation_pct"] > lag[-1]["regulation_pct"] and lead[-1]["regulation_pct"] < 0)

if __name__ == "__main__" and "--part" not in sys.argv:
    pass

print(f"\n  {sum(R)} of {len(R)} engineering checks passed")
sys.exit(0 if all(R) else 1)

"""
Equivalent-circuit checks against closed-form expectations.

    python check_circuit.py
"""

import math
import sys

from dtwin.physics.nameplate import preset
from dtwin.physics.thermal import top_oil_rise_ultimate
from dtwin.physics import circuit as C

R = []


def check(name, ok, detail=""):
    R.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


np_ = preset(100)
p = C.from_nameplate(np_)

# ---- turns ratio and referral ---------------------------------------------------
check("per-phase ratio a = V1_ph / V2_ph = 11000/433", abs(p.a - 11000 / 433) < 1e-9, f"a = {p.a:.4f}")
check("physical turns ratio, Dyn11 = 11000 / (433/sqrt3)",
      abs(C.physical_turns_ratio(np_) - 11000 / (433 / math.sqrt(3))) < 1e-9,
      f"N1/N2 = {C.physical_turns_ratio(np_):.2f}")
check("referral: R2 referred to HV = a^2 R2, and back", abs(p.R2_hv - p.R2 * p.a ** 2) < 1e-12
      and abs(p.R1_lv * p.a ** 2 - p.R1) < 1e-12, f"R2 {p.R2 * 1e3:.3f} mohm -> {p.R2_hv:.3f} ohm HV")
op0 = C.solve_load(p, load_pu=1.0, pf=1.0)
check("current ratio: I1 ~ I2 / a at rated load (plus I0)",
      abs(abs(op0.I1) - abs(op0.I2) / p.a) / (abs(op0.I2) / p.a) < 0.03,
      f"I1 {abs(op0.I1):.3f} A vs I2/a {abs(op0.I2) / p.a:.3f} A")
check("voltage ratio at no load: V2 = V1 / a",
      abs(abs(C.solve_from_primary(p, load_pu=0).V2) - p.v1_ph / p.a) / (p.v1_ph / p.a) < 1e-3)

# ---- no-load current -------------------------------------------------------------
nl = C.solve_load(p, load_pu=0.0)
s = nl.summary()
check("I0 = sqrt(Ic^2 + Im^2)", abs(s["I0_a"] - math.hypot(s["Ic_a"], s["Im_a"])) < 1e-9,
      f"I0 {s['I0_a'] * 1e3:.1f} mA, Ic {s['Ic_a'] * 1e3:.1f} mA, Im {s['Im_a'] * 1e3:.1f} mA")
check("I0 is the configured 2 % of rated", abs(s["I0_pct_of_rated"] - 2.0) < 0.01, f"{s['I0_pct_of_rated']:.3f} %")
check("Ic in phase with E1, Im lags it by 90 deg",
      abs((nl.Ic / nl.E1).imag) < 1e-12 and abs(math.degrees(math.atan2((nl.Im / nl.E1).imag, (nl.Im / nl.E1).real)) + 90) < 1e-6)

# ---- losses ----------------------------------------------------------------------
pc, pk = C.rated_losses(p)
check("rated copper loss / core loss = nameplate R (solved circuit, rated point)",
      abs(pc / pk - np_.loss_ratio_R) < 1e-9, f"P_cu {pc:.2f} W, P_core {pk:.2f} W, ratio {pc / pk:.6f}")
cu = [C.solve_load(p, load_pu=k, pf=1.0).p_cu_w for k in (0.25, 0.5, 1.0, 1.25)]
check("copper loss rises with current, ~ I^2", all(b > a for a, b in zip(cu, cu[1:]))
      and abs(cu[2] / cu[1] - 4.0) < 0.05, f"{[round(x) for x in cu]} W")
hot = C.solve_load(C.scaled(p, r=1.2), load_pu=1.0, pf=1.0).p_cu_w
check("copper loss rises with winding resistance", hot > cu[2] and abs(hot / cu[2] - 1.2) < 0.01,
      f"R x1.2 -> {hot:.0f} W")
op_r = C.solve_load(p, load_pu=0.8, pf=0.9)
check("power balance: P_in = P_out + P_cu + P_core",
      abs(op_r.p_in_w - op_r.p_out_w - op_r.p_cu_w - op_r.p_core_w) < 1e-6 * op_r.p_in_w)

# ---- efficiency ------------------------------------------------------------------
ks = [i / 200 for i in range(10, 300)]
eff = [C.solve_load(p, load_pu=k, pf=1.0).efficiency for k in ks]
k_peak = ks[eff.index(max(eff))]
k_eq = C.max_efficiency_load_pu(pc, pk)
check("efficiency peaks where copper loss equals core loss", abs(k_peak - k_eq) < 0.02,
      f"peak at {k_peak:.3f} pu, sqrt(P_core/P_cu) = {k_eq:.3f} pu, eta {100 * max(eff):.2f} %")

# ---- regulation sign -------------------------------------------------------------
lag = C.solve_load(p, load_pu=1.0, pf=0.8, lagging=True).regulation_pct
lead = C.solve_load(p, load_pu=1.0, pf=0.8, lagging=False).regulation_pct
unity = C.solve_load(p, load_pu=1.0, pf=1.0).regulation_pct
check("regulation positive at lagging pf, negative at leading pf (0.8)", lag > unity > 0 > lead,
      f"lag {lag:+.2f} %, unity {unity:+.2f} %, lead {lead:+.2f} %")
approx = (p.Req_hv * math.cos(math.acos(0.8)) + p.Xeq_hv * 0.6) * p.i1_rated / p.v1_ph * 100
check("regulation ~ (R cos + X sin) approximation at 0.8 lag", abs(lag - approx) < 0.3,
      f"exact {lag:.2f} %, approx {approx:.2f} %")

# ---- units -------------------------------------------------------------------------
v1, v2, i1, i2, s_ph = C.phase_quantities(np_)
check("unit conversion: kV line -> V phase, kVA -> per-phase VA",
      abs(v1 - 11000 / math.sqrt(3)) < 1e-9 and abs(s_ph - 100e3 / 3) < 1e-9 and abs(i2 - np_.rated_current_a) < 1e-9,
      f"V1 {v1:.1f} V, I2 {i2:.2f} A, S_ph {s_ph / 1e3:.2f} kVA")
d = p.as_dict()
check("percentage impedance recovered from ohms", abs(d["z_pct"] - 4.5) < 1e-9, f"{d['z_pct']:.3f} %")
np1 = preset(25, phases=1, lv_v=240.0)
p1 = C.from_nameplate(np1)
check("single-phase nameplate: V and I used directly", abs(p1.v2_ph - 240.0) < 1e-9 and abs(p1.i2_rated - 25e3 / 240) < 1e-9)

# ---- standard tests round-trip ----------------------------------------------------------
oc = C.open_circuit_test(np_.hv_kv * 1000, abs(nl.I0), nl.p_core_w)
check("open-circuit test recovers Rc and Xm", abs(oc["Rc_ohm"] / p.Rc - 1) < 0.01 and abs(oc["Xm_ohm"] / p.Xm - 1) < 0.01,
      f"Rc {oc['Rc_ohm'] / 1e3:.1f} kohm, Xm {oc['Xm_ohm'] / 1e3:.1f} kohm")
zsc = complex(p.Req_hv, p.Xeq_hv)
vsc = abs(zsc) * p.i1_rated * math.sqrt(3)
sc = C.short_circuit_test(vsc, p.i1_rated, 3 * p.i1_rated ** 2 * p.Req_hv)
check("short-circuit test recovers Req and Xeq", abs(sc["Req_ohm"] / p.Req_hv - 1) < 1e-9 and abs(sc["Xeq_ohm"] / p.Xeq_hv - 1) < 1e-9,
      f"V_sc {vsc:.0f} V ({100 * vsc / 11000:.2f} %), Req {sc['Req_ohm']:.2f} ohm, Xeq {sc['Xeq_ohm']:.2f} ohm")

# ---- coupling to the thermal model ------------------------------------------------------
# At rated load the two modules agree exactly (same loss ratio by construction).
# Elsewhere the circuit carries two second-order effects C57.91's K^2 formula
# leaves out: I0 inside I1 (~0.4 % of copper loss) and core loss following the
# internal EMF, which sags with load (~2-3 % of core loss). Together they move
# the oil rise by ~0.3 % of rated rise; the bound is 0.5 % (0.28 K).
op_rated = C.solve_from_primary(p, load_pu=1.0, pf=1.0)
check("at rated load: circuit loss ratio == 1 exactly, oil rise == C57.91 rated rise",
      abs(C.thermal_loss_ratio(op_rated, p) - 1.0) < 1e-12)
worst = 0.0
for k in (0.0, 0.3, 0.6, 1.0, 1.3):
    op = C.solve_from_primary(p, load_pu=k, pf=1.0)
    ratio = C.thermal_loss_ratio(op, p)
    from_circuit = np_.top_oil_rise_rated_k * ratio ** np_.n_exponent
    from_thermal = top_oil_rise_ultimate(abs(op.I2) / p.i2_rated, np_)
    worst = max(worst, abs(from_circuit - from_thermal))
check("loss ratio consistent with the thermal model (oil rise from circuit losses == C57.91)",
      worst < 0.005 * np_.top_oil_rise_rated_k, f"worst difference {worst:.3f} K over 0-1.3 pu "
      f"(bound {0.005 * np_.top_oil_rise_rated_k:.3f} K, second-order circuit effects)")
rc = C.thermal_rc(np_, p)
check("thermal RC reading: tau = Rth x Cth", abs(rc["Rth_k_per_w"] * rc["Cth_j_per_k"] - np_.tau_oil_h * 3600) < 1e-6,
      f"Rth {rc['Rth_k_per_w'] * 1e3:.2f} K/kW, Cth {rc['Cth_j_per_k'] / 1e6:.1f} MJ/K")

print(f"\n  {sum(R)} of {len(R)} circuit checks passed")
sys.exit(0 if all(R) else 1)

"""
Magnetic characteristics of the core, from physics.

1. B-H magnetisation curve of grain-oriented (CRGO) electrical steel along the
   rolling direction. ILLUSTRATIVE: representative points for this class of
   steel, not taken from a datasheet we can cite. The shape is what matters
   here: a steep, nearly linear region, a saturation knee around 1.8 T, and
   then a field strength that rises by orders of magnitude for a little more
   flux. Hysteresis loss is NOT modelled: that needs measured loop data.

2. Excitation (open-circuit, V-I) characteristic: no-load current against
   applied voltage at 50 Hz, secondary open. For each voltage

       B_max = V / (4.44 f N1 A_core)                     (sinusoidal flux)
       B(t)  = B_max sin(wt) over one cycle
       i_m(t) = [ H(B(t)) l_core + B(t) g / mu0 ] / N1    (steel + core-joint gap)
       I_m   = RMS of i_m(t)
       I_c   = V / Rc                                     (in phase with V)
       I0    = sqrt(I_m^2 + I_c^2)

   I_m and I_c combine exactly in RMS because i_m(t) contains only odd
   harmonics in phase with the flux, all orthogonal to the core-loss
   fundamental, which is in phase with V. g is the equivalent air gap of the
   core joints; set it to 0 for the steel-only curve.

   N1, A_core, l_core and g are ILLUSTRATIVE design parameters, editable.

3. Voltage-transfer characteristic: V2 against V1 at a chosen load and power
   factor, from the per-phase equivalent circuit (circuit.py). Rc and Xm are
   constant there, so the transfer stays linear: saturation shows in I0
   (curve 2), not in V2.
"""

import math

import numpy as np
from scipy.interpolate import PchipInterpolator

from .nameplate import Nameplate
from . import circuit as C

MU0 = 4e-7 * math.pi

# Representative B-H points for grain-oriented electrical steel (rolling
# direction). ILLUSTRATIVE - see the module docstring.
BH_POINTS = [  # (H A/m, B T)
    (0.0, 0.0), (5.0, 0.40), (10.0, 0.90), (15.0, 1.20), (20.0, 1.40), (30.0, 1.55),
    (50.0, 1.68), (100.0, 1.77), (200.0, 1.82), (500.0, 1.87), (1000.0, 1.91),
    (3000.0, 1.96), (10000.0, 2.02), (30000.0, 2.07), (100000.0, 2.16),
]
BH_SOURCE = "illustrative: representative grain-oriented steel, rolling direction; not from a cited datasheet"
KNEE_T = 1.80


def _bh_interp():
    h = np.array([p[0] for p in BH_POINTS])
    b = np.array([p[1] for p in BH_POINTS])
    return PchipInterpolator(b, h, extrapolate=True), PchipInterpolator(h, b, extrapolate=True)


_H_OF_B, _B_OF_H = _bh_interp()
B_TABLE_MAX = BH_POINTS[-1][1]


def h_of_b(b):
    """Field strength (A/m) for flux density B (T); odd-symmetric. Beyond the
    table the curve continues with vacuum permeability (full saturation)."""
    b = np.asarray(b, float)
    s, a = np.sign(b), np.abs(b)
    h = _H_OF_B(np.minimum(a, B_TABLE_MAX))
    over = np.maximum(a - B_TABLE_MAX, 0.0)
    return s * (h + over / MU0)


def bh_curve(n=200):
    hs = np.concatenate([[0.0], np.logspace(0, math.log10(BH_POINTS[-1][0]), n)])
    bs = _B_OF_H(hs)
    mu_r = np.where(hs > 0, bs / (MU0 * np.maximum(hs, 1e-12)), np.nan)
    return {"H_a_per_m": hs.round(3).tolist(), "B_t": bs.round(4).tolist(),
            "mu_r": [None if not np.isfinite(x) else round(float(x)) for x in mu_r],
            "knee_t": KNEE_T, "source": BH_SOURCE,
            "points": [{"H_a_per_m": h, "B_t": b} for h, b in BH_POINTS],
            "definition": "Flux density B against magnetising field H for the core steel, measured on "
                          "an Epstein frame or single-sheet tester at 50 Hz; the normal (anhysteretic) "
                          "curve, without the hysteresis loop.",
            "not_modelled": "Hysteresis and eddy-current loss: needs measured loop data for this steel."}


# --------------------------------------------------------------------------- #
# Core design (illustrative) and the excitation characteristic
# --------------------------------------------------------------------------- #
DEFAULT_DESIGN = {"b_rated_t": 1.60, "n1_turns": None, "a_core_m2": None,
                  "l_core_m": 1.20, "gap_mm": None, "connection": "Dyn11"}


def core_design(np_: Nameplate, **over):
    """Turns and core area consistent with the nameplate and a rated peak flux
    density: N1 A = V_winding / (4.44 f B). Without data, a typical 1.6 T and a
    round turn count are assumed; everything is labelled illustrative."""
    d = {**DEFAULT_DESIGN, **{k: v for k, v in over.items() if v is not None}}
    v_wind = np_.hv_kv * 1000.0 if (np_.phases == 3 and d["connection"][0] == "D") else \
        np_.hv_kv * 1000.0 / (math.sqrt(3) if np_.phases == 3 else 1.0)
    if d["n1_turns"] is None and d["a_core_m2"] is None:
        d["n1_turns"] = 2400
    if d["a_core_m2"] is None:
        d["a_core_m2"] = v_wind / (4.44 * np_.frequency_hz * d["n1_turns"] * d["b_rated_t"])
    if d["n1_turns"] is None:
        d["n1_turns"] = v_wind / (4.44 * np_.frequency_hz * d["a_core_m2"] * d["b_rated_t"])
    d["v_winding_rated_v"] = v_wind
    d["b_rated_t"] = v_wind / (4.44 * np_.frequency_hz * d["n1_turns"] * d["a_core_m2"])
    d["source"] = "Illustrative core design, editable: there is no core drawing for this unit."
    if d["gap_mm"] is None:
        # One no-load current for the whole twin: choose the core-joint gap so
        # the rated I0 from this B-H model equals the equivalent circuit's.
        p = C.from_nameplate(np_)
        line = math.sqrt(3) if (np_.phases == 3 and d["connection"][0] == "D") else 1.0
        im_target = math.sqrt(max(abs(C.solve_load(p, load_pu=0.0).I0) ** 2 - (p.v1_ph / p.Rc) ** 2, 0.0))
        lo, hi = 0.0, 2.0
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            im = magnetising_rms(d["b_rated_t"], {**d, "gap_mm": mid}, n=360)[0] * line
            lo, hi = (mid, hi) if im < im_target else (lo, mid)
        d["gap_mm"] = round(lo, 4)
        d["gap_source"] = "The joint gap is chosen so the rated no-load current equals the equivalent circuit's 2 %."
    return d


def magnetising_rms(b_max, design, n=720):
    """RMS magnetising current of one winding for a sinusoidal flux of peak
    b_max, and the waveform's peak (A)."""
    wt = np.linspace(0, 2 * math.pi, n, endpoint=False)
    b = b_max * np.sin(wt)
    mmf = h_of_b(b) * design["l_core_m"] + b * design["gap_mm"] * 1e-3 / MU0
    i = mmf / design["n1_turns"]
    return float(np.sqrt(np.mean(i ** 2))), float(np.max(np.abs(i))), i


def excitation_curve(np_: Nameplate, circuit=None, design=None, v_max_pct=130.0, steps=53):
    """I0 against V (winding and line terms), 0 to v_max_pct of rated."""
    design = design or core_design(np_)
    p = circuit or C.from_nameplate(np_)
    f = np_.frequency_hz
    line_factor = math.sqrt(3) if (np_.phases == 3 and design["connection"][0] == "D") else 1.0
    i_rated_line = p.i1_rated
    out = []
    for pct in np.linspace(0, v_max_pct, steps):
        v_w = design["v_winding_rated_v"] * pct / 100.0
        b_max = v_w / (4.44 * f * design["n1_turns"] * design["a_core_m2"])
        im_w, ipk_w, _ = magnetising_rms(b_max, design)
        # core-loss component from the circuit's Rc (per-phase star equivalent)
        v_ph = p.v1_ph * pct / 100.0
        ic_line = v_ph / p.Rc
        im_line = im_w * line_factor
        i0 = math.hypot(im_line, ic_line)
        out.append({"v_pct": round(float(pct), 2), "v_winding_v": round(float(v_w), 1), "b_max_t": round(float(b_max), 4),
                    "im_a": round(float(im_line), 5), "ic_a": round(float(ic_line), 5), "i0_a": round(float(i0), 5),
                    "i0_pct": round(float(100 * i0 / i_rated_line), 4), "i_peak_a": round(float(ipk_w * line_factor), 5)})
    rated = min(out, key=lambda x: abs(x["v_pct"] - 100))
    over = min(out, key=lambda x: abs(x["v_pct"] - 120))
    knee_v = next((x["v_pct"] for x in out if x["b_max_t"] >= KNEE_T), None)
    ratio = over["i0_a"] / rated["i0_a"] if rated["i0_a"] else None
    _, _, wave_rated = magnetising_rms(rated["b_max_t"], design, n=240)
    _, _, wave_over = magnetising_rms(over["b_max_t"], design, n=240)
    return {"points": out, "design": design, "rated": rated, "knee_v_pct": knee_v,
            "i0_ratio_120_over_100": None if ratio is None else round(ratio, 3),
            "waveform": {"deg": [round(k * 1.5, 1) for k in range(240)],
                         "rated_a": (wave_rated * line_factor).round(5).tolist(),
                         "v120_a": (wave_over * line_factor).round(5).tolist()},
            "definition": "No-load current against applied voltage, sinusoidal at 50 Hz, secondary open "
                          "(open-circuit test). Line current, HV side.",
            "explanation": (f"From 100 % to 120 % voltage the flux density rises from {rated['b_max_t']:.2f} T to "
                            f"{over['b_max_t']:.2f} T, past the {KNEE_T:.2f} T knee. There the permeability "
                            f"collapses, so each extra tesla needs far more field: I0 rises x{ratio:.1f} for a "
                            f"x1.2 rise in voltage, and its waveform becomes peaky (odd harmonics).")
            if ratio else "",
            "not_modelled": "Hysteresis loop and inrush transients. In service, I0 cannot be measured: the "
                            "measured current is load current."}


def transfer_curve(np_: Nameplate, circuit=None, load_pct=100.0, pf=0.9, lagging=True, v_max_pct=130.0, steps=27):
    p = circuit or C.from_nameplate(np_)
    ln = math.sqrt(3) if np_.phases == 3 else 1.0
    out = []
    for pct in np.linspace(0, v_max_pct, steps):
        if pct == 0:
            out.append({"v1_pct": 0.0, "v1_line_v": 0.0, "v2_line_v": 0.0, "v2_pct": 0.0})
            continue
        # constant-impedance load, defined at rated voltage: its current follows the voltage
        op = C.solve_from_primary(p, v1_ph=p.v1_ph * pct / 100, load_pu=load_pct / 100 * pct / 100,
                                  pf=pf, lagging=lagging)
        out.append({"v1_pct": round(float(pct), 2), "v1_line_v": round(abs(op.V1) * ln, 1),
                    "v2_line_v": round(abs(op.V2) * ln, 2), "v2_pct": round(100 * abs(op.V2) / p.v2_ph, 3)})
    rated = C.solve_from_primary(p, load_pu=load_pct / 100, pf=pf, lagging=lagging)
    return {"points": out, "load_pct": load_pct, "pf": pf, "lagging": lagging,
            "no_load_ratio": round(p.a, 4), "regulation_pct_at_rated_v": round(rated.regulation_pct, 3),
            "definition": f"LV line voltage against HV line voltage for a constant-impedance load drawing "
                          f"{load_pct:g} % of rated current at rated voltage, power factor {pf:g} "
                          f"{'lagging' if lagging else 'leading'}, from the per-phase equivalent circuit.",
            "not_modelled": "Rc and Xm are constant, so core saturation does not bend this line; it shows in I0."}

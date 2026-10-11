"""
Per-phase equivalent circuit of the transformer, solved with phasors.

    V1 --R1--jX1--+--R2'--jX2'-- V2' = a V2          (all referred to HV)
                  |
               Rc || jXm         magnetising branch at the internal EMF E1

A three-phase unit is analysed on its per-phase star equivalent, as standard:
phase voltage V_LL / sqrt3 on both sides, phase current = line current, a
per-phase ratio a = V1_ph / V2_ph, and three-phase quantities = 3 x per-phase.
A single-phase unit (nameplate.phases = 1) uses V and I directly. The physical
turns ratio depends on the winding connection; for the usual Dyn11
distribution unit it is V1_LL / (V2_LL / sqrt3), reported separately.

Naming: R1, X1, Rc, Xm are HV-side ohms; R2, X2 are LV-side ohms. Anything
referred across carries a `_hv` or `_lv` suffix. All quantities are RMS at
50 Hz.

Losses feed the thermal model. Rated core loss is set to rated copper loss / R
(the nameplate loss ratio the thermal model already uses), so that

    (P_cu + P_core) / (P_cu_rated + P_core_rated)  ==  (K^2 R + 1) / (R + 1)

and the two modules cannot disagree (check_circuit.py tests it).

Parameter sources: unless test data are supplied, the percentage impedance,
losses and no-load current are typical values for a 100 kVA 11 kV / 433 V ONAN
unit, labelled ILLUSTRATIVE. IS 1180 specifies maximum losses and impedance for
this class; we do not cite a table we have not checked. Open- and short-
circuit test calculations below turn real test data into calibrated values.
"""

import cmath
import math
from dataclasses import dataclass, field

from .nameplate import Nameplate

SQRT3 = math.sqrt(3.0)
ILLUSTRATIVE = "illustrative"


# --------------------------------------------------------------------------- #
# Parameters
# --------------------------------------------------------------------------- #
@dataclass
class CircuitParams:
    R1: float          # HV winding resistance, ohm (per phase, star equivalent)
    X1: float          # HV leakage reactance, ohm
    R2: float          # LV winding resistance, ohm
    X2: float          # LV leakage reactance, ohm
    Rc: float          # core-loss resistance, HV side, ohm
    Xm: float          # magnetising reactance, HV side, ohm
    a: float           # per-phase voltage ratio V1_ph / V2_ph
    phases: int = 3
    v1_ph: float = 0.0  # rated phase voltages and currents
    v2_ph: float = 0.0
    i1_rated: float = 0.0
    i2_rated: float = 0.0
    s_rated_va: float = 0.0
    source: dict = field(default_factory=dict)    # parameter -> "illustrative" / "test" / "nameplate"

    # referral: impedance scales with a^2 across the ideal transformer
    @property
    def R2_hv(self):
        return self.R2 * self.a ** 2

    @property
    def X2_hv(self):
        return self.X2 * self.a ** 2

    @property
    def R1_lv(self):
        return self.R1 / self.a ** 2

    @property
    def X1_lv(self):
        return self.X1 / self.a ** 2

    @property
    def Req_hv(self):
        return self.R1 + self.R2_hv

    @property
    def Xeq_hv(self):
        return self.X1 + self.X2_hv

    @property
    def z_base_hv(self):
        return self.v1_ph ** 2 / (self.s_rated_va / self.phases)

    def as_dict(self):
        return {"R1_ohm_hv": self.R1, "X1_ohm_hv": self.X1, "R2_ohm_lv": self.R2, "X2_ohm_lv": self.X2,
                "Rc_ohm_hv": self.Rc, "Xm_ohm_hv": self.Xm, "a_per_phase": self.a,
                "Req_ohm_hv": self.Req_hv, "Xeq_ohm_hv": self.Xeq_hv,
                "z_pct": 100 * math.hypot(self.Req_hv, self.Xeq_hv) / self.z_base_hv,
                "r_pct": 100 * self.Req_hv / self.z_base_hv, "source": dict(self.source)}


def phase_quantities(np_: Nameplate):
    """Rated per-phase voltages and currents for the star equivalent."""
    ph = np_.phases
    v1 = np_.hv_kv * 1000.0 / (SQRT3 if ph == 3 else 1.0)
    v2 = np_.lv_v / (SQRT3 if ph == 3 else 1.0)
    s_ph = np_.rating_kva * 1000.0 / ph
    return v1, v2, s_ph / v1, s_ph / v2, s_ph


def physical_turns_ratio(np_: Nameplate, connection="Dyn11"):
    """N1/N2 of the actual windings. Dyn: HV delta (winding sees V_LL), LV star
    (winding sees V_LL/sqrt3)."""
    if np_.phases == 1:
        return np_.hv_kv * 1000.0 / np_.lv_v
    hv_w = np_.hv_kv * 1000.0 if connection[0] == "D" else np_.hv_kv * 1000.0 / SQRT3
    lv_w = np_.lv_v / SQRT3 if connection[1:2] in ("y", "z") else np_.lv_v
    return hv_w / lv_w


DEFAULT_Z_PCT, DEFAULT_I0_PCT = 4.5, 2.0


def from_nameplate(np_: Nameplate, z_pct=None, p_cu_w=None, p_core_w=None, i0_pct=None):
    """Build the circuit from ratings plus percentage impedance, losses and
    no-load current. Rated core loss defaults to copper loss / R, keeping the
    thermal model's loss ratio. R and X are split equally between the windings
    when referred to one side (the usual assumption without separate tests).

    A nameplate calibrated from a test report (np_.test_data, see
    dtwin/calibrate) supplies any of the four that the caller does not."""
    td = getattr(np_, "test_data", None) or {}
    v1, v2, i1, i2, s_ph = phase_quantities(np_)
    ph = np_.phases
    src = {}
    for k in ("z_pct", "p_cu_w", "p_core_w", "i0_pct"):
        if locals()[k] is None and td.get(k) is not None:
            src[k] = td.get("source", {}).get(k, "test report")
    z_pct = td.get("z_pct") if z_pct is None and td.get("z_pct") is not None else z_pct
    p_cu_w = td.get("p_cu_w") if p_cu_w is None and td.get("p_cu_w") is not None else p_cu_w
    p_core_w = td.get("p_core_w") if p_core_w is None and td.get("p_core_w") is not None else p_core_w
    i0_pct = td.get("i0_pct") if i0_pct is None and td.get("i0_pct") is not None else i0_pct
    if p_cu_w is None:
        p_cu_w = 0.013 * np_.rating_kva * 1000.0              # 1.3 % of rating
        src["p_cu_w"] = ILLUSTRATIVE
    else:
        src.setdefault("p_cu_w", "given")
    if p_core_w is None:
        p_core_w = p_cu_w / np_.loss_ratio_R
        src["p_core_w"] = "copper loss / nameplate R"
    else:
        src.setdefault("p_core_w", "given")
    if z_pct is None:
        z_pct = DEFAULT_Z_PCT
    if i0_pct is None:
        i0_pct = DEFAULT_I0_PCT
    src.setdefault("z_pct", ILLUSTRATIVE if z_pct == 4.5 else "given")
    src.setdefault("i0_pct", ILLUSTRATIVE if i0_pct == 2.0 else "given")

    z_base = v1 ** 2 / s_ph
    req = (p_cu_w / ph) / i1 ** 2
    zeq = z_pct / 100.0 * z_base
    if zeq <= req:
        raise ValueError("percentage impedance too small for the copper loss")
    xeq = math.sqrt(zeq ** 2 - req ** 2)
    a = v1 / v2
    rc = v1 ** 2 / (p_core_w / ph)
    ic = v1 / rc
    i0 = i0_pct / 100.0 * i1
    if i0 <= ic:
        raise ValueError("no-load current smaller than its core-loss component")
    im = math.sqrt(i0 ** 2 - ic ** 2)
    xm = v1 / im
    c = CircuitParams(R1=req / 2, X1=xeq / 2, R2=req / 2 / a ** 2, X2=xeq / 2 / a ** 2,
                      Rc=rc, Xm=xm, a=a, phases=ph, v1_ph=v1, v2_ph=v2, i1_rated=i1, i2_rated=i2,
                      s_rated_va=s_ph * ph, source=src)
    # Calibrate on the SOLVED circuit at rated load and rated voltage: the
    # primary current carries I0 and core loss follows the internal EMF, so the
    # closed-form values above miss the targets by a fraction of a percent.
    # Scale winding resistance and Rc until copper and core loss hit them, so
    # the copper/core ratio is exactly the nameplate R the thermal model uses.
    for _ in range(20):
        op = solve_from_primary(c, load_pu=1.0, pf=1.0)
        fr, fc = p_cu_w / op.p_cu_w, op.p_core_w / p_core_w
        c = scaled(c, r=fr, rc=fc)
        c = scaled(c, x=math.sqrt(zeq ** 2 - c.Req_hv ** 2) / c.Xeq_hv)     # keep |Z| = z_pct
        if abs(fr - 1) < 1e-12 and abs(fc - 1) < 1e-12:
            break
    return c


def scaled(p: CircuitParams, r=1.0, x=1.0, rc=1.0, xm=1.0):
    """A copy with winding resistance, leakage reactance, Rc or Xm scaled."""
    return CircuitParams(R1=p.R1 * r, X1=p.X1 * x, R2=p.R2 * r, X2=p.X2 * x, Rc=p.Rc * rc, Xm=p.Xm * xm,
                         a=p.a, phases=p.phases, v1_ph=p.v1_ph, v2_ph=p.v2_ph, i1_rated=p.i1_rated,
                         i2_rated=p.i2_rated, s_rated_va=p.s_rated_va, source=dict(p.source))


# --------------------------------------------------------------------------- #
# Operating point
# --------------------------------------------------------------------------- #
@dataclass
class OperatingPoint:
    V1: complex
    I1: complex
    E1: complex
    I0: complex
    Ic: complex
    Im: complex
    V2: complex
    I2: complex
    pf: float
    lagging: bool
    p: CircuitParams

    @property
    def p_cu_w(self):
        return self.p.phases * (abs(self.I1) ** 2 * self.p.R1 + abs(self.I2) ** 2 * self.p.R2)

    @property
    def p_core_w(self):
        return self.p.phases * abs(self.E1) ** 2 / self.p.Rc

    @property
    def p_out_w(self):
        return self.p.phases * (self.V2 * self.I2.conjugate()).real

    @property
    def p_in_w(self):
        return self.p.phases * (self.V1 * self.I1.conjugate()).real

    @property
    def efficiency(self):
        return self.p_out_w / self.p_in_w if self.p_in_w > 0 else float("nan")

    @property
    def regulation_pct(self):
        """(no-load secondary voltage - full-load) / full-load, with the
        primary voltage held: no-load secondary is |V1| / a."""
        v2_nl = abs(self.V1) / self.p.a
        return 100.0 * (v2_nl - abs(self.V2)) / abs(self.V2)

    @property
    def drop_hv_v(self):
        return abs(self.I1 * complex(self.p.R1, self.p.X1))

    @property
    def drop_lv_v(self):
        return abs(self.I2 * complex(self.p.R2, self.p.X2))

    def summary(self):
        p = self.p
        ln = SQRT3 if p.phases == 3 else 1.0
        return {
            "V1_phase_v": abs(self.V1), "V1_line_v": abs(self.V1) * ln,
            "V2_phase_v": abs(self.V2), "V2_line_v": abs(self.V2) * ln,
            "I1_a": abs(self.I1), "I2_a": abs(self.I2), "load_pu": abs(self.I2) / p.i2_rated,
            "I0_a": abs(self.I0), "Ic_a": abs(self.Ic), "Im_a": abs(self.Im),
            "I0_pct_of_rated": 100.0 * abs(self.I0) / p.i1_rated,
            "drop_hv_winding_v": self.drop_hv_v, "drop_lv_winding_v": self.drop_lv_v,
            "p_cu_w": self.p_cu_w, "p_core_w": self.p_core_w,
            "p_in_w": self.p_in_w, "p_out_w": self.p_out_w,
            "efficiency_pct": 100.0 * self.efficiency, "regulation_pct": self.regulation_pct,
            "pf": self.pf, "lagging": self.lagging,
        }


def solve_load(p: CircuitParams, v2_ph=None, i2_a=None, load_pu=None, pf=0.9, lagging=True):
    """Forward solve from the secondary: terminal voltage V2 (phase, LV side,
    default rated), load current I2 (A) or as a fraction of rated, power factor."""
    v2 = p.v2_ph if v2_ph is None else v2_ph
    if i2_a is None:
        i2_a = (load_pu or 0.0) * p.i2_rated
    phi = math.acos(max(min(pf, 1.0), 0.0)) * (-1 if lagging else 1)
    V2 = complex(v2, 0.0)
    I2 = cmath.rect(i2_a, phi)
    V2r, I2r = V2 * p.a, I2 / p.a
    E1 = V2r + I2r * complex(p.R2_hv, p.X2_hv)
    Ic = E1 / p.Rc
    Im = E1 / complex(0.0, p.Xm)
    I0 = Ic + Im
    I1 = I2r + I0
    V1 = E1 + I1 * complex(p.R1, p.X1)
    return OperatingPoint(V1, I1, E1, I0, Ic, Im, V2, I2, pf, lagging, p)


def solve_from_primary(p: CircuitParams, v1_ph=None, load_pu=0.0, pf=0.9, lagging=True, iters=30):
    """Given the primary phase voltage (default rated) and a load current
    fraction, find the secondary voltage by fixed-point iteration."""
    v1 = p.v1_ph if v1_ph is None else v1_ph
    v2 = v1 / p.a
    op = None
    for _ in range(iters):
        op = solve_load(p, v2_ph=v2, load_pu=load_pu, pf=pf, lagging=lagging)
        err = v1 / abs(op.V1)
        v2 *= err
        if abs(err - 1) < 1e-10:
            break
    return op


def max_efficiency_load_pu(p_cu_rated_w, p_core_w):
    """Copper loss equals core loss at K = sqrt(P_core / P_cu_rated)."""
    return math.sqrt(p_core_w / p_cu_rated_w)


# --------------------------------------------------------------------------- #
# Standard tests: turn test data into parameters
# --------------------------------------------------------------------------- #
def open_circuit_test(v_line, i_line, p_total_w, phases=3):
    """Rated voltage on one winding, other open. Returns Rc and Xm referred to
    the energised side, per phase, plus the current components."""
    v = v_line / (SQRT3 if phases == 3 else 1.0)
    p = p_total_w / phases
    ic = p / v
    if i_line <= ic:
        raise ValueError("open-circuit current smaller than its loss component")
    im = math.sqrt(i_line ** 2 - ic ** 2)
    return {"Rc_ohm": v / ic, "Xm_ohm": v / im, "Ic_a": ic, "Im_a": im,
            "pf_no_load": p / (v * i_line)}


def short_circuit_test(v_line, i_line, p_total_w, phases=3):
    """One winding shorted, reduced voltage raises rated current. Returns Req
    and Xeq referred to the energised side, per phase."""
    v = v_line / (SQRT3 if phases == 3 else 1.0)
    p = p_total_w / phases
    zeq = v / i_line
    req = p / i_line ** 2
    if req > zeq:
        raise ValueError("short-circuit loss inconsistent with voltage and current")
    return {"Req_ohm": req, "Xeq_ohm": math.sqrt(zeq ** 2 - req ** 2), "Zeq_ohm": zeq}


# --------------------------------------------------------------------------- #
# Coupling to the thermal model, and its RC reading
# --------------------------------------------------------------------------- #
def rated_losses(p: CircuitParams):
    """Copper and core loss at rated current, rated primary voltage, unity pf:
    the point the thermal model's rated rises refer to."""
    op = solve_from_primary(p, load_pu=1.0, pf=1.0)
    return op.p_cu_w, op.p_core_w


def thermal_loss_ratio(op: OperatingPoint, p_rated: CircuitParams = None):
    """Total loss relative to rated total loss: the quantity the C57.91 oil
    rise is driven by, (K^2 R + 1) / (R + 1) when losses scale as assumed."""
    pc, pk = rated_losses(p_rated or op.p)
    return (op.p_cu_w + op.p_core_w) / (pc + pk)


def thermal_rc(np_: Nameplate, p: CircuitParams):
    """C57.91's first-order oil model read as a thermal RC network: the oil
    rise settles at losses x Rth (to the power n), with time constant
    tau = Rth x Cth. At rated losses: Rth = rated rise / rated total loss."""
    pc, pk = rated_losses(p)
    rth = np_.top_oil_rise_rated_k / (pc + pk)
    cth = np_.tau_oil_h * 3600.0 / rth
    return {"Rth_k_per_w": rth, "Cth_j_per_k": cth, "tau_oil_h": np_.tau_oil_h,
            "rated_total_loss_w": pc + pk, "n": np_.n_exponent,
            "rule": "rise_ult = rise_rated x (P_total / P_total_rated)^n ; tau = Rth x Cth"}

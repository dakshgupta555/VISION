"""
Calibrate the twin from a transformer's factory test report.

A report (YAML or JSON, the same schema as the dashboard form) gives the
nameplate and any of: the open-circuit test (no-load loss P0, no-load current
I0), the short-circuit test (load loss Pk at a stated reference temperature,
impedance voltage) and the heat-run (temperature-rise) test. Every field is
optional except the rating and the two voltages; anything missing falls back
to today's built-in value and is flagged ASSUMED.

    python -m dtwin.calibrate reports/sample_100kva_ILLUSTRATIVE.yaml --show

With no report applied nothing here runs and the twin is byte-identical to
before (check_calibration.py proves it, and that a report holding exactly the
built-in values reproduces today's outputs).

What is derived, and how (each parameter carries a provenance badge
NAMEPLATE / TEST REPORT / DERIVED / ASSUMED and its formula):

  core loss        P0 from the open-circuit test
  copper loss      Pk from the short-circuit test, moved to the model's
                   reference temperature when the report states another:
                     k = (T + theta_model) / (T + theta_report)
                     with the I2R share known:  Pk' = I2R*k + (Pk - I2R)/k
                     without it (all treated as I2R, ASSUMED):  Pk' = Pk*k
                   T is the conductor's temperature constant (copper 234.5 K
                   from alpha20 = 0.00393 /K: T = 1/alpha20 - 20; aluminium
                   must be given in the report, it is not assumed here)
  impedance        short-circuit impedance voltage, else the nameplate %Z
  loss ratio       R = Pk' / P0
  top-oil rise     the twin keeps its design cooling (the built-in rated rise
                   at the built-in losses for this rating); the report's losses
                   change the heat put in, through C57.91's loss scaling:
                     rise = 55 K x ((P0 + Pk') / (P0_default + Pk_default))^n
                   and the heat-run's measured rise is shown beside it as a
                   CHECK. Nothing is tuned to make the two agree.
  hot-spot         gradient over top oil from the heat run when given (directly,
                   or hot-spot rise over ambient minus measured top-oil rise, or
                   H x (average winding rise - average oil rise) when the report
                   gives the hot-spot factor H); otherwise the built-in 25 K
  time constants   oil and winding, from the heat run when given

No limit from a standard is used unless config/standards.yaml holds it with a
source; otherwise the compliance check reads "not checked".
"""

import copy
import hashlib
import json
import math
from pathlib import Path

import yaml

from ..physics import circuit as C
from ..physics.nameplate import Nameplate, preset

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = {"100 kVA (illustrative)": ROOT / "reports" / "sample_100kva_ILLUSTRATIVE.yaml",
           "250 kVA (synthetic)": ROOT / "reports" / "sample_250kva_SYNTHETIC.yaml"}
STANDARDS = ROOT / "config" / "standards.yaml"

# The temperature the twin's rated copper loss refers to. A modelling choice,
# not a value quoted from a standard: set it to the reference temperature your
# standard and your test report use.
MODEL_REF_TEMP_C = 75.0
COPPER_T_K = 234.5            # 1 / 0.00393 - 20, annealed copper (alpha20 = 0.00393 /K)
DEFAULT_TOP_OIL_RISE_K = 55.0  # the built-in design values (dtwin/physics/nameplate.py)

NAMEPLATE, TEST, DERIVED, ASSUMED = "NAMEPLATE", "TEST REPORT", "DERIVED", "ASSUMED"


class ReportError(ValueError):
    """The report cannot be used as written; the message says what to fix."""


# ------------------------------------------------------------------ reading
def load(path):
    p = Path(path)
    text = p.read_text()
    try:
        d = json.loads(text) if p.suffix.lower() == ".json" else yaml.safe_load(text)
    except (ValueError, yaml.YAMLError) as e:
        raise ReportError(f"{p.name}: not valid {'JSON' if p.suffix.lower() == '.json' else 'YAML'}: {e}") from None
    return d


def parse_text(text):
    """Pasted text: JSON if it parses as JSON, else YAML."""
    try:
        return json.loads(text)
    except ValueError:
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ReportError(f"report is neither valid JSON nor valid YAML: {e}") from None


def _num(sec, key, where, positive=True, allow_zero=False):
    v = (sec or {}).get(key)
    if v is None or v == "":
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ReportError(f"{where}.{key} must be a number, got {v!r}") from None
    if not math.isfinite(v):
        raise ReportError(f"{where}.{key} must be finite")
    if positive and (v < 0 or (v == 0 and not allow_zero)):
        raise ReportError(f"{where}.{key} must be {'at least' if allow_zero else 'above'} 0, got {v:g}")
    return v


def defaults(rating_kva, hv_kv=11.0, lv_v=433.0):
    """Today's built-in values for this rating: the 'was' column and the
    fallback for every missing field."""
    np_ = preset(rating_kva, hv_kv=hv_kv, lv_v=lv_v)
    p_cu = 0.013 * rating_kva * 1000.0
    return {"rating_kva": rating_kva, "hv_kv": hv_kv, "lv_v": lv_v, "frequency_hz": np_.frequency_hz,
            "cooling": np_.cooling, "vector_group": "Dyn11",
            "p_core_w": p_cu / np_.loss_ratio_R, "p_cu_w": p_cu, "z_pct": C.DEFAULT_Z_PCT, "i0_pct": C.DEFAULT_I0_PCT,
            "loss_ratio_R": np_.loss_ratio_R, "top_oil_rise_rated_k": np_.top_oil_rise_rated_k,
            "hotspot_rise_rated_k": np_.hotspot_rise_rated_k, "tau_oil_h": np_.tau_oil_h,
            "tau_winding_min": np_.tau_winding_min, "n_exponent": np_.n_exponent, "m_exponent": np_.m_exponent}


# ------------------------------------------------------------------ calibrate
class Calibration:
    """The result of one report: parameters with provenance, the calibrated
    nameplate, and the checks."""

    def __init__(self, report):
        if not isinstance(report, dict):
            raise ReportError("a report must be a mapping with a 'nameplate' section")
        self.report = copy.deepcopy(report)
        meta = report.get("report") or {}
        self.name = str(meta.get("name") or "unnamed test report")
        npl = report.get("nameplate") or {}
        oc = report.get("open_circuit") or {}
        sc = report.get("short_circuit") or {}
        hr = report.get("heat_run") or {}
        for sec, nm in ((npl, "nameplate"), (oc, "open_circuit"), (sc, "short_circuit"), (hr, "heat_run")):
            if not isinstance(sec, dict):
                raise ReportError(f"'{nm}' must be a mapping")
        kva = _num(npl, "rating_kva", "nameplate")
        hv = _num(npl, "hv_kv", "nameplate")
        lv = _num(npl, "lv_v", "nameplate")
        miss = [k for k, v in (("rating_kva", kva), ("hv_kv", hv), ("lv_v", lv)) if v is None]
        if miss:
            raise ReportError(f"nameplate needs {', '.join(miss)}: the rating and both voltages are required")
        if hv * 1000.0 <= lv:
            raise ReportError(f"voltage mismatch: HV {hv:g} kV is not above LV {lv:g} V")
        D = defaults(kva, hv, lv)
        self.defaults = D
        P = {}

        def put(key, label, value, unit, prov, formula="", note=""):
            P[key] = {"key": key, "label": label, "value": value, "unit": unit, "provenance": prov,
                      "was": D.get(key), "formula": formula, "note": note}

        put("rating_kva", "Rating", kva, "kVA", NAMEPLATE)
        put("hv_kv", "HV voltage (line)", hv, "kV", NAMEPLATE)
        put("lv_v", "LV voltage (line)", lv, "V", NAMEPLATE)
        f = _num(npl, "frequency_hz", "nameplate")
        put("frequency_hz", "Frequency", f if f else D["frequency_hz"], "Hz", NAMEPLATE if f else ASSUMED)
        cool = npl.get("cooling")
        put("cooling", "Cooling", cool or D["cooling"], "", NAMEPLATE if cool else ASSUMED,
            note="" if not cool or cool == "ONAN" else "exponents n, m kept at the built-in ONAN values")
        vg = npl.get("vector_group")
        put("vector_group", "Vector group", vg or D["vector_group"], "", NAMEPLATE if vg else ASSUMED)

        # ---- open circuit -------------------------------------------------------
        p0 = _num(oc, "p0_w", "open_circuit")
        if p0 is not None and p0 == 0:
            raise ReportError("open_circuit.p0_w must be above 0")
        v_oc = _num(oc, "voltage_v", "open_circuit")
        if v_oc is not None and abs(v_oc - lv) / lv > 0.02:
            raise ReportError(f"voltage mismatch: no-load loss measured at {v_oc:g} V, but the rated LV voltage is "
                              f"{lv:g} V. Give P0 at rated voltage.")
        put("p_core_w", "Core (no-load) loss", p0 if p0 is not None else D["p_core_w"], "W",
            TEST if p0 is not None else ASSUMED, "P0 from the open-circuit test" if p0 is not None
            else "built-in: copper loss / R")
        i0p, i0a = _num(oc, "i0_pct", "open_circuit"), _num(oc, "i0_a", "open_circuit")
        if i0p is not None:
            put("i0_pct", "No-load current", i0p, "% of rated", TEST, "I0 from the open-circuit test")
        elif i0a is not None:
            side = str(oc.get("i0_side", "lv")).lower()
            if side not in ("lv", "hv"):
                raise ReportError("open_circuit.i0_side must be 'lv' or 'hv'")
            rated = kva * 1000.0 / (math.sqrt(3) * (lv if side == "lv" else hv * 1000.0))
            put("i0_pct", "No-load current", 100.0 * i0a / rated, "% of rated", DERIVED,
                f"100 x I0 / I_rated,{side} = 100 x {i0a:g} / {rated:.2f} A")
        else:
            put("i0_pct", "No-load current", D["i0_pct"], "% of rated", ASSUMED, "built-in illustrative value")

        # ---- short circuit -------------------------------------------------------
        pk = _num(sc, "pk_w", "short_circuit")
        if pk is not None and pk == 0:
            raise ReportError("short_circuit.pk_w must be above 0")
        tref = _num(sc, "reference_temp_c", "short_circuit", positive=False)
        i2r = _num(sc, "i2r_w", "short_circuit")
        mat = str(sc.get("winding_material") or "").lower()
        T = _num(sc, "temperature_constant_k", "short_circuit")
        conv = None
        if pk is not None and tref is not None and tref != MODEL_REF_TEMP_C:
            if T is None:
                if mat in ("", "copper", "cu"):
                    T = COPPER_T_K
                else:
                    raise ReportError(f"short_circuit: give temperature_constant_k for {mat} windings to move the "
                                      f"load loss from {tref:g} to {MODEL_REF_TEMP_C:g} C (not assumed here)")
            k = (T + MODEL_REF_TEMP_C) / (T + tref)
            if i2r is not None:
                if i2r > pk:
                    raise ReportError(f"short_circuit.i2r_w {i2r:g} W is larger than the load loss {pk:g} W")
                val = i2r * k + (pk - i2r) / k
                formula = (f"Pk' = I2R x k + (Pk - I2R) / k, k = (T + {MODEL_REF_TEMP_C:g}) / (T + {tref:g}) = {k:.5f}, "
                           f"T = {T:g} K: {i2r:g} x {k:.5f} + {pk - i2r:g} / {k:.5f} = {val:.1f} W")
                prov = DERIVED
            else:
                val = pk * k
                formula = (f"Pk' = Pk x k, k = (T + {MODEL_REF_TEMP_C:g}) / (T + {tref:g}) = {k:.5f}, T = {T:g} K: "
                           f"{pk:g} x {k:.5f} = {val:.1f} W (ASSUMED all of Pk is I2R: no i2r_w given)")
                prov = DERIVED
            conv = {"k": k, "T_k": T, "from_c": tref, "to_c": MODEL_REF_TEMP_C, "pk_w": pk, "pk_ref_w": val}
            put("p_cu_w", "Copper (load) loss at rated current", val, "W", prov, formula,
                note=f"measured {pk:g} W at {tref:g} C")
        elif pk is not None:
            put("p_cu_w", "Copper (load) loss at rated current", pk, "W", TEST,
                f"Pk from the short-circuit test, at {tref:g} C (the model's reference)" if tref is not None
                else "Pk from the short-circuit test",
                note="" if tref is not None else f"reference temperature not stated: ASSUMED {MODEL_REF_TEMP_C:g} C")
        else:
            put("p_cu_w", "Copper (load) loss at rated current", D["p_cu_w"], "W", ASSUMED,
                "built-in: 1.3 % of rating")
        self.conversion = conv
        zsc, znp = _num(sc, "impedance_pct", "short_circuit"), _num(npl, "impedance_pct", "nameplate")
        for z, nm in ((zsc, "short_circuit"), (znp, "nameplate")):
            if z is not None and not 0 < z < 100:
                raise ReportError(f"{nm}.impedance_pct must be between 0 and 100, got {z:g}")
        if zsc is not None:
            put("z_pct", "Impedance", zsc, "%", TEST, "impedance voltage from the short-circuit test")
        elif znp is not None:
            put("z_pct", "Impedance", znp, "%", NAMEPLATE, "nameplate impedance")
        else:
            put("z_pct", "Impedance", D["z_pct"], "%", ASSUMED, "built-in illustrative value")

        pcu, pfe = P["p_cu_w"]["value"], P["p_core_w"]["value"]
        both_assumed = P["p_cu_w"]["provenance"] == ASSUMED and P["p_core_w"]["provenance"] == ASSUMED
        # exactly the built-in losses -> exactly the built-in R (x / (x / 5) need not be 5.0 in floating point)
        R = D["loss_ratio_R"] if (pcu, pfe) == (D["p_cu_w"], D["p_core_w"]) else pcu / pfe
        put("loss_ratio_R", "Loss ratio R", R, "", ASSUMED if both_assumed else DERIVED,
            f"R = Pk' / P0 = {pcu:.1f} / {pfe:.1f} = {R:.3f}")

        # ---- heat run ---------------------------------------------------------------
        n = D["n_exponent"]
        p_def = D["p_cu_w"] + D["p_core_w"]
        if both_assumed:
            rise = D["top_oil_rise_rated_k"]
            put("top_oil_rise_rated_k", "Top-oil rise at rated load (model)", rise, "K", ASSUMED, "built-in design value")
        else:
            ratio = (pcu + pfe) / p_def
            rise = DEFAULT_TOP_OIL_RISE_K if ratio == 1.0 else DEFAULT_TOP_OIL_RISE_K * ratio ** n
            put("top_oil_rise_rated_k", "Top-oil rise at rated load (model)", rise, "K", DERIVED,
                f"55 K x ((P0 + Pk') / (P0 + Pk)_built-in)^n = 55 x ({pcu + pfe:.1f} / {p_def:.1f})^{n:g} = {rise:.2f} K",
                note="design cooling kept; losses from the report (IEEE C57.91 loss scaling)")
        meas_to = _num(hr, "top_oil_rise_k", "heat_run")
        self.measured_top_oil_rise_k = meas_to
        grad_g = _num(hr, "hotspot_gradient_k", "heat_run")
        hs_rise = _num(hr, "hotspot_rise_k", "heat_run")
        H = _num(hr, "hotspot_factor_H", "heat_run")
        w_avg, o_avg = _num(hr, "average_winding_rise_k", "heat_run"), _num(hr, "average_oil_rise_k", "heat_run")
        self.hotspot_note = None
        if grad_g is not None:
            put("hotspot_rise_rated_k", "Hot-spot gradient over top oil", grad_g, "K", TEST, "hot-spot gradient from the heat run")
        elif hs_rise is not None and meas_to is not None:
            g = hs_rise - meas_to
            if g < 0:
                raise ReportError(f"heat_run: hot-spot rise {hs_rise:g} K is below the top-oil rise {meas_to:g} K")
            put("hotspot_rise_rated_k", "Hot-spot gradient over top oil", g, "K", DERIVED,
                f"hot-spot rise - top-oil rise = {hs_rise:g} - {meas_to:g} = {g:g} K")
        elif H is not None and w_avg is not None and o_avg is not None:
            g = H * (w_avg - o_avg)
            put("hotspot_rise_rated_k", "Hot-spot gradient over top oil", g, "K", DERIVED,
                f"H x (average winding rise - average oil rise) = {H:g} x ({w_avg:g} - {o_avg:g}) = {g:.2f} K")
        else:
            put("hotspot_rise_rated_k", "Hot-spot gradient over top oil", D["hotspot_rise_rated_k"], "K", ASSUMED,
                "built-in design value")
            if w_avg is not None:
                self.hotspot_note = (f"average winding rise {w_avg:g} K given; a hot-spot gradient needs the hot-spot "
                                     "factor H and the average oil rise, which the report does not give (not assumed)")
        for key, label, unit in (("tau_oil_h", "Oil time constant", "h"), ("tau_winding_min", "Winding time constant", "min")):
            v = _num(hr, key, "heat_run")
            put(key, label, v if v is not None else D[key], unit, TEST if v is not None else ASSUMED,
                "from the heat run" if v is not None else "built-in design value")
        for key, label in (("n_exponent", "Oil exponent n"), ("m_exponent", "Winding exponent m")):
            put(key, label, D[key], "", ASSUMED, "built-in IEEE C57.91 ONAN exponent")
        self.params = P
        self.nameplate()                     # builds the circuit once: inconsistent data fail here

    # -------------------------------------------------------------- the twin
    def nameplate(self, tag=None):
        """The calibrated nameplate. Circuit data ride on test_data."""
        P = self.params
        v = lambda k: P[k]["value"]
        np_ = preset(v("rating_kva"), hv_kv=v("hv_kv"), lv_v=v("lv_v"), frequency_hz=v("frequency_hz"),
                     cooling=v("cooling"), loss_ratio_R=v("loss_ratio_R"),
                     top_oil_rise_rated_k=v("top_oil_rise_rated_k"), hotspot_rise_rated_k=v("hotspot_rise_rated_k"),
                     tau_oil_h=v("tau_oil_h"), tau_winding_min=v("tau_winding_min"))
        if tag:
            np_.tag = tag
        src = {"p_cu_w": P["p_cu_w"]["provenance"].lower(), "p_core_w": P["p_core_w"]["provenance"].lower(),
               "z_pct": P["z_pct"]["provenance"].lower(), "i0_pct": P["i0_pct"]["provenance"].lower()}
        np_.test_data = {"p_cu_w": v("p_cu_w"), "p_core_w": v("p_core_w"), "z_pct": v("z_pct"),
                         "i0_pct": v("i0_pct"), "source": src, "report": self.name}
        try:
            self._circuit = C.from_nameplate(np_)
        except ValueError as e:
            raise ReportError(f"the report's values are not consistent with each other: {e} "
                              f"(impedance {v('z_pct'):g} %, load loss {v('p_cu_w'):.0f} W, I0 {v('i0_pct'):g} %, "
                              f"no-load loss {v('p_core_w'):.0f} W)") from None
        return np_

    def check_voltages(self, lv_v, hv_kv=None, where="the data file"):
        """The report must describe the transformer the data come from."""
        r_lv = self.params["lv_v"]["value"]
        if lv_v and abs(r_lv - lv_v) / lv_v > 0.05:
            raise ReportError(f"voltage mismatch: the report's LV is {r_lv:g} V but {where} is {lv_v:g} V")
        r_hv = self.params["hv_kv"]["value"]
        if hv_kv and abs(r_hv - hv_kv) / hv_kv > 0.05:
            raise ReportError(f"voltage mismatch: the report's HV is {r_hv:g} kV but {where} is {hv_kv:g} kV")

    @property
    def key(self):
        """Stable identity, for caching twins built from this report."""
        return hashlib.sha1(json.dumps(self.report, sort_keys=True, default=str).encode()).hexdigest()[:12]

    # -------------------------------------------------------------- checks
    def checks(self):
        P = self.params
        np_ = self.nameplate()
        p = C.from_nameplate(np_)
        out = []
        model = P["top_oil_rise_rated_k"]["value"]
        meas = self.measured_top_oil_rise_k
        out.append({"key": "top_oil_rise", "label": "Top-oil rise at rated load: heat run vs model",
                    "measured": meas, "model": round(model, 2), "unit": "K",
                    "difference": None if meas is None else round(model - meas, 2),
                    "text": ("no heat-run top-oil rise in the report" if meas is None else
                             f"model {model:.1f} K vs measured {meas:g} K: {model - meas:+.1f} K. The model keeps its "
                             "design cooling; a large difference says the unit cools better or worse than that design.")})
        R = P["loss_ratio_R"]["value"]
        out.append({"key": "loss_ratio", "label": "Loss ratio R = Pk / P0", "measured": round(R, 3),
                    "model": self.defaults["loss_ratio_R"], "unit": "",
                    "difference": round(R - self.defaults["loss_ratio_R"], 3),
                    "text": f"R {R:.2f} against the built-in {self.defaults['loss_ratio_R']:g}"})
        z = P["z_pct"]["value"]
        znp = _num(self.report.get("nameplate") or {}, "impedance_pct", "nameplate")
        zsc = _num(self.report.get("short_circuit") or {}, "impedance_pct", "short_circuit")
        out.append({"key": "impedance", "label": "Impedance", "measured": z, "model": round(p.as_dict()["z_pct"], 4),
                    "unit": "%", "difference": None if znp is None or zsc is None else round(zsc - znp, 3),
                    "text": (f"test {zsc:g} % vs nameplate {znp:g} %" if znp is not None and zsc is not None
                             else f"{z:g} % ({P['z_pct']['provenance']}); circuit solves to {p.as_dict()['z_pct']:.3f} %")})
        for pf, lag in ((1.0, False), (0.8, True)):
            op = C.solve_from_primary(p, load_pu=1.0, pf=pf, lagging=lag)
            simple = 100.0 * P["rating_kva"]["value"] * 1000 * pf / (P["rating_kva"]["value"] * 1000 * pf
                                                                        + P["p_core_w"]["value"] + P["p_cu_w"]["value"])
            out.append({"key": f"efficiency_pf{pf:g}", "label": f"Efficiency at full load, pf {pf:g}",
                        "measured": round(simple, 3), "model": round(100 * op.efficiency, 3), "unit": "%",
                        "difference": None,
                        "text": f"S cos(phi) / (S cos(phi) + P0 + Pk') = {simple:.2f} %; equivalent circuit "
                                f"{100 * op.efficiency:.2f} %"})
        out.append(self.compliance())
        return out

    def compliance(self):
        """Measured losses against the standard's limit for this rating, only
        when config/standards.yaml holds that limit with its source."""
        kva = self.params["rating_kva"]["value"]
        row, src = None, None
        try:
            std = yaml.safe_load(STANDARDS.read_text()) or {}
            for r in std.get("loss_limits") or []:
                if float(r.get("rating_kva", -1)) == kva:
                    row, src = r, std.get("source")
        except (OSError, yaml.YAMLError, TypeError, ValueError):
            pass
        lim50, lim100 = (row or {}).get("max_total_loss_50pct_w"), (row or {}).get("max_total_loss_100pct_w")
        if row is None or (lim50 in (None, "") and lim100 in (None, "")):
            return {"key": "standard", "label": "Losses vs IS 1180 limit", "measured": None, "model": None,
                    "unit": "W", "difference": None, "status": "not checked",
                    "text": f"not checked: no verified IS 1180 limit for {kva:g} kVA in config/standards.yaml"}
        p0, pcu = self.params["p_core_w"]["value"], self.params["p_cu_w"]["value"]
        t50, t100 = p0 + 0.25 * pcu, p0 + pcu
        parts = []
        for t, lim, nm in ((t50, lim50, "50 %"), (t100, lim100, "100 %")):
            if lim not in (None, ""):
                parts.append(f"total loss at {nm} load {t:.0f} W vs limit {float(lim):g} W "
                             f"({'within' if t <= float(lim) else 'ABOVE'})")
        return {"key": "standard", "label": "Losses vs IS 1180 limit", "measured": round(t100), "model": lim100,
                "unit": "W", "difference": None, "status": "checked", "source": src or row.get("source"),
                "text": "; ".join(parts) + ". A check, not a compliance certificate."}

    def as_json(self):
        rows = []
        for p in self.params.values():
            was, val = p["was"], p["value"]
            change = None
            if isinstance(val, (int, float)) and isinstance(was, (int, float)) and not isinstance(val, bool):
                change = round(val - was, 4)
            rows.append({**p, "value": round(val, 4) if isinstance(val, float) else val,
                         "was": round(was, 4) if isinstance(was, float) else was, "change": change})
        return {"name": self.name, "key": self.key, "illustrative": bool((self.report.get("report") or {}).get("illustrative")),
                "note": (self.report.get("report") or {}).get("note"), "params": rows, "checks": self.checks(),
                "conversion": self.conversion, "hotspot_note": self.hotspot_note,
                "model_reference_temp_c": MODEL_REF_TEMP_C}


def builtin_report(rating_kva=100.0, hv_kv=11.0, lv_v=433.0):
    """A report holding exactly today's built-in values (the round-trip test)."""
    D = defaults(rating_kva, hv_kv, lv_v)
    return {"report": {"name": f"built-in values, {rating_kva:g} kVA", "illustrative": True},
            "nameplate": {"rating_kva": rating_kva, "hv_kv": hv_kv, "lv_v": lv_v, "frequency_hz": D["frequency_hz"],
                          "cooling": D["cooling"], "vector_group": D["vector_group"]},
            "open_circuit": {"p0_w": D["p_core_w"], "i0_pct": D["i0_pct"]},
            "short_circuit": {"pk_w": D["p_cu_w"], "reference_temp_c": MODEL_REF_TEMP_C, "impedance_pct": D["z_pct"]},
            "heat_run": {"hotspot_gradient_k": D["hotspot_rise_rated_k"], "tau_oil_h": D["tau_oil_h"],
                         "tau_winding_min": D["tau_winding_min"]}}


# ------------------------------------------------------------------ active report
_ACTIVE = {"cal": None}


def active():
    return _ACTIVE["cal"]


def apply(cal):
    _ACTIVE["cal"] = cal
    return cal


def reset():
    _ACTIVE["cal"] = None

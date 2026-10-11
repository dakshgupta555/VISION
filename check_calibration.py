"""
Calibration from a factory test report: checks.

    python check_calibration.py            # all checks (the first exports tag pre-calibrate to compare against)
    python check_calibration.py --quick    # skip the pre-calibrate comparison

  1  default unchanged: with no report, every engine output is byte-identical
     to the code at tag pre-calibrate (git archive export, same dump, compared;
     wall-clock timing fields such as fem solve_ms excluded, they differ run to run)
  2  round trip: a report holding exactly the built-in values reproduces
     today's outputs (provenance labels on the circuit aside, by design)
  3  apply then reset restores today's outputs exactly
  4  20 % higher Pk: copper loss, top-oil rise up and efficiency down, by the formula
  5  missing fields fall back to the built-in values and say ASSUMED
  6  reference-temperature conversion matches a hand calculation
  7  bad input gives a clear error, no crash
  8  the CLI runs on both samples; the standards row reads "not checked" while empty
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCEN = [("showcase", (200, 1500, 2200, -1)), ("winter_250kva_5min", (500, -1))]
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)


# ------------------------------------------------------------------ the dump
def dump(fem=True):
    """Every engine output the dashboard reads, as canonical JSON text. Imports
    happen here so --dump can run against another checkout (DTWIN_ROOT)."""
    from dtwin.api import app as A
    out = {}
    for name, cursors in SCEN:
        tw = A._twin(name)
        n = len(tw.rows)
        out[f"{name}/rows"] = hashlib.sha1(tw.rows.to_csv().encode()).hexdigest()
        out[f"{name}/timeline"] = A.timeline(name)
        out[f"{name}/alerts"] = A.alerts(name)
        for c in cursors:
            t = str(c % n)
            out[f"{name}/{t}/state"] = A.state(name, t)
            out[f"{name}/{t}/series"] = A.series(name, t)
            out[f"{name}/{t}/whatif"] = A.whatif(name, t)
            out[f"{name}/{t}/components"] = A.components(name, t)
            out[f"{name}/{t}/diagnose"] = A.diagnose_route(name, t)
            out[f"{name}/{t}/losses"] = A.losses_route(name, t)
        out[f"{name}/curves"] = A.curves_route(name)
        if fem:
            out[f"{name}/fem"] = A.fem_route(name, str(cursors[0] % n))
    return out


def untimed(d):
    """Wall-clock timings (e.g. fem solve_ms) are not results: they differ run to run."""
    if isinstance(d, dict):
        return {k: untimed(v) for k, v in d.items() if not k.endswith("_ms")}
    if isinstance(d, list):
        return [untimed(x) for x in d]
    return d


def canon(d):
    return json.dumps(untimed(d), sort_keys=True, default=str)


def drop_labels(d):
    """Remove the circuit's provenance labels ('illustrative' vs 'test report'):
    they are meant to change when a report is loaded; the numbers are not."""
    if isinstance(d, dict):
        return {k: drop_labels(v) for k, v in d.items()
                if not (k == "source" and isinstance(v, dict) and "p_cu_w" in v)}
    if isinstance(d, list):
        return [drop_labels(x) for x in d]
    return d


def first_diff(a, b, path=""):
    if type(a) is not type(b):
        return f"{path}: {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}/{k}: missing on one side"
            r = first_diff(a[k], b[k], f"{path}/{k}")
            if r:
                return r
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: length {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            r = first_diff(x, y, f"{path}[{i}]")
            if r:
                return r
        return None
    return None if a == b else f"{path}: {a!r} vs {b!r}"


# ------------------------------------------------------------------ checks
def main():
    quick = "--quick" in sys.argv
    from dtwin import calibrate as K
    from dtwin.api import app as A
    from dtwin.physics import circuit as C

    K.reset()
    base = json.loads(canon(dump()))

    # 1 against the code before this feature existed
    if quick:
        print("  SKIP  1 byte-identical to tag pre-calibrate (--quick)")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            exp = Path(tmp) / "pre"
            exp.mkdir()
            arc = subprocess.run(["git", "archive", "pre-calibrate"], cwd=ROOT, capture_output=True)
            ok = arc.returncode == 0
            if ok:
                subprocess.run(["tar", "-x", "-C", str(exp)], input=arc.stdout, check=True)
                outp = Path(tmp) / "pre.json"
                env = {**os.environ, "DTWIN_ROOT": str(exp), "PYTHONDONTWRITEBYTECODE": "1"}
                r = subprocess.run([sys.executable, str(ROOT / "check_calibration.py"), "--dump", str(outp)],
                                   cwd=exp, env=env, capture_output=True, text=True, timeout=1800)
                ok = r.returncode == 0
                pre = outp.read_text() if ok else ""
            same = ok and pre == canon(base)
            check("1 no report loaded: every engine output byte-identical to tag pre-calibrate", same,
                  f"{len(base)} outputs, sha1 {hashlib.sha1(canon(base).encode()).hexdigest()[:10]}" if same else
                  (first_diff(json.loads(pre), base) if ok else f"export/dump failed: {(r.stderr if arc.returncode == 0 else arc.stderr)[-300:]}"))

    # 2 round trip with the built-in values
    for kva, name in ((100.0, "showcase"), (250.0, "winter_250kva_5min")):
        cal = K.Calibration(K.builtin_report(kva))
        prov = {p["key"]: p["provenance"] for p in cal.as_json()["params"]}
        np_c, np_d = cal.nameplate(), __import__("dtwin.physics.nameplate", fromlist=["preset"]).preset(kva)
        same_np = all(getattr(np_c, f) == getattr(np_d, f) for f in np_d.__dataclass_fields__ if f not in ("test_data",))
        check(f"2 built-in {kva:g} kVA report -> nameplate identical to the preset, field by field", same_np,
              f"provenance: losses {prov['p_cu_w']}/{prov['p_core_w']}, R {prov['loss_ratio_R']}")
    K.apply(K.Calibration(K.builtin_report(100.0)))
    rt = json.loads(canon({k: v for k, v in dump(fem=True).items() if k.startswith("showcase")}))
    K.reset()
    b100 = {k: v for k, v in base.items() if k.startswith("showcase")}
    d = first_diff(drop_labels(b100), drop_labels(rt))
    check("2 built-in report applied: every showcase output equals today's (circuit provenance labels aside)",
          d is None, d or f"{len(rt)} outputs compared")
    labels_changed = canon(b100) != canon(rt)
    check("2 ... and the provenance labels do say the values came from the report", labels_changed)

    # 3 apply then reset
    K.apply(K.Calibration(K.load(K.SAMPLES["100 kVA (illustrative)"])))
    changed = canon(dump(fem=False)) != canon({k: v for k, v in base.items() if not k.endswith("/fem")})
    K.reset()
    back = canon(dump()) == canon(base)
    check("3 a sample report changes the outputs; reset restores today's outputs byte for byte", changed and back)

    # 4 20 % higher Pk
    rep = K.builtin_report(100.0)
    hi = json.loads(json.dumps(rep)); hi["short_circuit"]["pk_w"] = rep["short_circuit"]["pk_w"] * 1.2
    c0, c1 = K.Calibration(rep), K.Calibration(hi)
    v = lambda c, k: c.params[k]["value"]                    # raw, not the rounded display value
    p0, pk = rep["open_circuit"]["p0_w"], rep["short_circuit"]["pk_w"]
    expect_rise = 55.0 * ((p0 + 1.2 * pk) / (p0 + pk)) ** 0.8
    eff = lambda c: next(x["model"] for x in c.checks() if x["key"] == "efficiency_pf1")
    ok = (abs(v(c1, "p_cu_w") - 1.2 * pk) < 1e-9 and abs(v(c1, "top_oil_rise_rated_k") - expect_rise) < 1e-9
          and v(c1, "top_oil_rise_rated_k") > v(c0, "top_oil_rise_rated_k") and eff(c1) < eff(c0)
          and abs(v(c1, "loss_ratio_R") - 1.2 * pk / p0) < 1e-9)
    check("4 Pk +20 %: copper loss x1.2, R x1.2, top-oil rise = 55 x ((P0+1.2Pk)/(P0+Pk))^0.8, efficiency down", ok,
          f"rise {v(c0, 'top_oil_rise_rated_k'):.2f} -> {v(c1, 'top_oil_rise_rated_k'):.3f} K (expected {expect_rise:.3f}), "
          f"efficiency {eff(c0):.3f} -> {eff(c1):.3f} %")
    K.apply(c1)
    tw1 = A._twin("showcase")
    K.reset()
    tw0 = A._twin("showcase")
    i = int(tw0.rows.K_oil.values.argmax())                  # the peak-load sample
    hs0, hs1 = float(tw0.rows.pred_hotspot_c.iloc[i]), float(tw1.rows.pred_hotspot_c.iloc[i])
    check("4 ... and the twin's predicted hot-spot at the peak-load sample is higher", hs1 > hs0, f"{hs0:.2f} -> {hs1:.2f} C")

    # 5 missing fields
    m = K.Calibration({"nameplate": {"rating_kva": 100, "hv_kv": 11, "lv_v": 433}, "open_circuit": {"p0_w": 240}})
    pr = {p["key"]: (p["provenance"], p["value"], p["was"]) for p in m.as_json()["params"]}
    assumed = [k for k in ("p_cu_w", "z_pct", "i0_pct", "hotspot_rise_rated_k", "tau_oil_h", "tau_winding_min",
                           "frequency_hz", "cooling", "vector_group") if pr[k][0] == "ASSUMED" and pr[k][1] == pr[k][2]]
    check("5 a report with only P0: every other field is the built-in value, flagged ASSUMED",
          len(assumed) == 9 and pr["p_core_w"][0] == "TEST REPORT" and pr["loss_ratio_R"][0] == "DERIVED", ", ".join(assumed))

    # 6 reference temperature
    rep6 = {"nameplate": {"rating_kva": 100, "hv_kv": 11, "lv_v": 433},
            "short_circuit": {"pk_w": 1200, "reference_temp_c": 20, "i2r_w": 1000, "winding_material": "copper"}}
    k = (234.5 + 75) / (234.5 + 20)
    hand = 1000 * k + 200 / k
    got = v(K.Calibration(rep6), "p_cu_w")
    rep6b = json.loads(json.dumps(rep6)); del rep6b["short_circuit"]["i2r_w"]
    got_b = v(K.Calibration(rep6b), "p_cu_w")
    check("6 Pk 1200 W at 20 C (I2R 1000 W) -> 75 C: 1000 k + 200 / k with k = 309.5 / 254.5",
          abs(got - hand) < 1e-9 and abs(got_b - 1200 * k) < 1e-9, f"{got:.3f} W (hand {hand:.3f}); all-I2R {got_b:.3f} W")

    # 7 bad input
    bad = {"negative loss": {"nameplate": {"rating_kva": 100, "hv_kv": 11, "lv_v": 433}, "open_circuit": {"p0_w": -5}},
           "impedance 0": {"nameplate": {"rating_kva": 100, "hv_kv": 11, "lv_v": 433}, "short_circuit": {"impedance_pct": 0}},
           "no-load test voltage mismatch": {"nameplate": {"rating_kva": 100, "hv_kv": 11, "lv_v": 433},
                                             "open_circuit": {"p0_w": 250, "voltage_v": 415 * 0.9}},
           "HV below LV": {"nameplate": {"rating_kva": 100, "hv_kv": 0.2, "lv_v": 433}},
           "missing kVA": {"nameplate": {"hv_kv": 11, "lv_v": 433}},
           "aluminium without T": {"nameplate": {"rating_kva": 100, "hv_kv": 11, "lv_v": 433},
                                   "short_circuit": {"pk_w": 1300, "reference_temp_c": 30, "winding_material": "aluminium"}},
           "impedance too small for the load loss": {"nameplate": {"rating_kva": 100, "hv_kv": 11, "lv_v": 433},
                                                     "short_circuit": {"pk_w": 5000, "impedance_pct": 2}},
           "not a mapping": "rating 100 kVA"}
    msgs = {}
    for nm, r in bad.items():
        try:
            K.Calibration(r)
            msgs[nm] = None
        except K.ReportError as e:
            msgs[nm] = str(e)
        except Exception as e:                                   # a crash is a failure
            msgs[nm] = None
            print(f"        crash on {nm}: {type(e).__name__}: {e}")
    ok = all(msgs.values())
    check("7 bad input -> a clear ReportError, never a crash", ok, f"{sum(bool(x) for x in msgs.values())}/{len(msgs)}")
    for nm, msg in msgs.items():
        print(f"        {nm}: {msg}")
    cal = K.Calibration({"nameplate": {"rating_kva": 100, "hv_kv": 11, "lv_v": 415 * 0.9}})
    try:
        A._twin  # the data file is 433 V
        from dtwin.api.engine import get_twin
        get_twin("showcase", None, calibration=cal)
        vm = None
    except K.ReportError as e:
        vm = str(e)
    check("7 report LV voltage vs the data file's: refused with a clear message", vm and "voltage mismatch" in vm, vm)
    try:
        K.parse_text("nameplate: [unclosed")
        pe = None
    except K.ReportError as e:
        pe = str(e)
    check("7 pasted text that is neither JSON nor YAML: clear message", bool(pe), (pe or "")[:80])

    # 8 CLI and standards
    outs = [subprocess.run([sys.executable, "-m", "dtwin.calibrate", str(p), "--show"], cwd=ROOT, capture_output=True,
                           text=True, timeout=120) for p in K.SAMPLES.values()]
    check("8 python -m dtwin.calibrate <sample> --show runs on both samples",
          all(o.returncode == 0 and "Calibrated from test report" in o.stdout for o in outs))
    comp = K.Calibration(K.load(K.SAMPLES["100 kVA (illustrative)"])).compliance()
    check("8 config/standards.yaml empty -> the compliance row reads 'not checked'", comp.get("status") == "not checked",
          comp["text"])

    K.reset()
    n_ok = sum(results)
    print(f"\n{n_ok} of {len(results)} calibration checks passed")
    sys.exit(0 if n_ok == len(results) else 1)


if __name__ == "__main__":
    if "--dump" in sys.argv:
        root = os.environ.get("DTWIN_ROOT")
        if root:
            sys.path.insert(0, root)
            os.chdir(root)
        out = sys.argv[sys.argv.index("--dump") + 1]
        Path(out).write_text(canon(dump()))
    else:
        main()

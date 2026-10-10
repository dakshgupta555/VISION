"""
Engineering checks: characteristic curves, losses, diagnostics, agents and
corrective actions.

    python check_engineering.py
"""

import math
import sys

import numpy as np
import pandas as pd

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

# ---- component diagnostics ------------------------------------------------------------------
import copy
from dtwin.live.store import analyse
from dtwin.twin import components as TC
from dtwin.twin.diagnose import diagnose

a = {c["id"]: c for c in TC.assess(TC.from_twin(tw, tw.locate("2026-04-02 10:00")))["components"]}
check("core grey in service: the measured current is load current", a["core"]["state"] == "grey"
      and "load current" in a["core"]["reason"], a["core"]["reason"][:70])
ev = TC.from_twin(tw, tw.locate("2026-04-02 10:00"))
nl = C.solve_load(p0, load_pu=0.0)
ev["oc_test"] = {"v_line": np_.hv_kv * 1000, "i_line": abs(nl.I0), "p_w": nl.p_core_w, "side": "HV"}
core_oc = next(c for c in TC.assess(ev)["components"] if c["id"] == "core")
check("core assessable once open-circuit test data are supplied", core_oc["state"] == "green", core_oc["reason"])
check("healthy connections: all three LV phases green", all(a[f"lv_bushing_{p}"]["state"] == "green" for p in "ryb"))

df = tw.df.copy()
df["VY"] = df["VY"] - 0.06 * df["IY"]                  # a 60 mohm joint on the Y-phase path
twj = analyse(df.loc["2026-04-01":"2026-04-06"], 100.0, "simulator")
aj = {c["id"]: c for c in TC.assess(TC.from_twin(twj, len(twj.rows) - 1))["components"]}
check("a high-resistance joint on one phase turns only that connection yellow",
      aj["lv_bushing_y"]["state"] == "yellow" and aj["lv_bushing_r"]["state"] == "green" and aj["lv_bushing_b"]["state"] == "green",
      aj["lv_bushing_y"]["rule"]["substituted"])

# state transitions through the showcase cooling fault: green -> yellow -> red, in order, never skipping back
seq = []
for t in pd.date_range("2026-04-20 12:00", "2026-04-23 00:00", freq="2h"):
    st = next(c for c in TC.assess(TC.from_twin(tw, tw.locate(t)))["components"] if c["id"] == "radiators")["state"]
    if not seq or seq[-1] != st:
        seq.append(st)
check("radiators move green -> yellow -> red as the cooling fault develops", seq[:3] == ["green", "yellow", "red"], " -> ".join(seq))

# every finding is placed on a component that is itself flagged, and is supported
bad = []
for t in ["2026-04-23 15:00", "2026-04-14 17:00", "2026-04-10 12:00", "2026-04-17 12:00", "2026-04-28 03:00"]:
    d = diagnose(tw, tw.locate(t))
    comps = {c["id"]: c for c in d["components"]["components"]}
    for f in d["findings"]:
        top = f["ranked"][0]
        if comps[f["component_id"]]["state"] in ("green", "grey") or not f["evidence"] or (top["likelihood"] != "excluded" and not top["supporting"] and top["likelihood"] == "more likely"):
            bad.append((t, f["condition"], f["component_id"]))
        if any(h["likelihood"] == "excluded" and not h["against"] for h in f["ranked"]):
            bad.append((t, f["condition"], "excluded without a reason"))
check("findings sit on flagged components; top hypotheses carry evidence; exclusions give a reason", not bad, str(bad[:3]))
d = diagnose(tw, tw.locate("2026-04-23 15:00"))
cool = next(f for f in d["findings"] if f["condition"] == "Cooling path degraded")
check("cooling: gradual onset ranks fouling / oil loss above a closed valve; fans excluded on ONAN",
      cool["ranked"][0]["likelihood"] == "more likely" and cool["ranked"][-1]["likelihood"] == "excluded"
      and next(h for h in cool["ranked"] if "valve" in h["hypothesis"])["likelihood"] == "less likely")

# ---- structured agents -------------------------------------------------------------------------
from dtwin.agents import run_agents
from dtwin.agents.schema import validate, DISCLAIMER
from dtwin.twin.actions import catalogue

moments = ["2026-04-02 10:00", "2026-04-10 12:00", "2026-04-14 17:00", "2026-04-17 12:00", "2026-04-23 15:00", "2026-04-28 03:00"]
runs = {t: run_agents(tw, tw.locate(t)) for t in moments}
errs = [(t, a, e) for t, r in runs.items() for a, recs in r["agents"].items() for rec in recs for e in validate(rec)]
check("agents: every record fits the schema, at every moment", not errs,
      f"{sum(len(v) for r in runs.values() for v in r['agents'].values())} records; {errs[:2]}")
check("agents: deterministic (same input, same output)", run_agents(tw, tw.locate("2026-04-23 15:00")) == runs["2026-04-23 15:00"])
check("agents: every record carries the advisory disclaimer (never operates equipment, never authorises service)",
      all(rec["disclaimer"] == DISCLAIMER for r in runs.values() for recs in r["agents"].values() for rec in recs)
      and "never operates equipment" in DISCLAIMER and "never authorises" in DISCLAIMER)

untraced, numbers_missing = [], []
for t, r in runs.items():
    diag = {d["component_id"]: d for d in r["agents"]["diagnostic"]}
    for rec in r["agents"]["recommendation"]:
        d = diag[rec["component_id"]]
        known = set(d["evidence_ids"])
        ids = [i for c in rec["corrective"]["conditions"] for i in c["evidence_ids"]]
        if not ids or not set(ids) <= known or not rec["recommended_action"]:
            untraced.append((t, rec["component_id"]))
    for d in r["agents"]["diagnostic"]:
        if d["state"] in ("yellow", "red") and not any(ch.isdigit() for ch in d["explanation"]):
            numbers_missing.append((t, d["component_id"]))
check("every recommendation traces to evidence ids present in its diagnostic record", not untraced, str(untraced[:3]))
check("explanations are templates filled with computed numbers", not numbers_missing, str(numbers_missing[:3]))
rec_states = {rec["state"] for r in runs.values() for rec in r["agents"]["recommendation"]}
diag_flag = {(t, d["component_id"]) for t, r in runs.items() for d in r["agents"]["diagnostic"] if d["state"] in ("yellow", "red", "sensor")}
rec_flag = {(t, x["component_id"]) for t, r in runs.items() for x in r["agents"]["recommendation"]}
check("recommendations exist exactly for yellow, red and instrument-fault components", diag_flag == rec_flag and rec_states <= {"yellow", "red", "sensor"},
      f"{len(rec_flag)} recommendations")

# ---- corrective actions ------------------------------------------------------------------------
cat = catalogue()
cat_items = {(m["item"], m["specification"]) for c in cat["conditions"].values() for m in c.get("materials", [])}
shown = {(m["item"], m["specification"]) for r in runs.values() for rec in r["agents"]["recommendation"]
         for c in rec["corrective"]["conditions"] for m in c["materials"]}
check("materials come only from config/catalogue.yaml", shown <= cat_items, f"{len(shown)} distinct items shown")
cool_rec = next(x for x in runs["2026-04-23 15:00"]["agents"]["recommendation"] if x["component_id"] == "radiators")
leak = next(c for c in cool_rec["corrective"]["conditions"] if c["key"] == "oil_level_check")
check("oil leak: indication distinguished from the confirmed source",
      "INDICATION" in leak["indication_vs_source"] and "CONFIRMED" in leak["indication_vs_source"])
wr = cat["conditions"]["winding_repair"]["materials"][0]
check("winding work: conductor grades and sizes stated as not interchangeable", "NOT interchangeable" in wr["note"])
TBF = "to be filled from approved vendor data"
unsourced = [k for k, c in cat["conditions"].items() if not c.get("source")]
unsourced += [m["item"] for c in cat["conditions"].values() for m in c.get("materials", [])
              if not m.get("source") or any(m.get(f) != TBF for f in ("supplier", "part_no", "price_inr"))]
check("catalogue: every condition cites a source; every material's supplier, part number and price "
      f"read '{TBF}'", not unsourced, str(unsourced[:3]))
check("every corrective condition lists inspection, corrective action, qualification and safety",
      all(c["inspection"] and c["corrective"] and c["qualification"] is not None and "safety" in c
          for r in runs.values() for rec in r["agents"]["recommendation"] for c in rec["corrective"]["conditions"]))

print(f"\n  {sum(R)} of {len(R)} engineering checks passed")
sys.exit(0 if all(R) else 1)

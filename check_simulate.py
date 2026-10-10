"""
Simulate workspace checks.

    python check_simulate.py

  1  isolation    a simulation never mutates observed data (rows, df, nameplate)
  2  assets       simulating one asset never changes another's state
  3  live         simulating on the live buffer leaves the buffer untouched
  4  identity     with no edits, the loss-driven trajectory equals the C57.91
                  K-based steady state the rest of the twin uses
  5  physics      +30 % load raises copper loss by ~1.69x and the hot-spot;
                  a hotter ambient shifts temperatures by the same amount;
                  worse cooling raises oil, not the core loss
  6  dynamics     the response is stepped in time: it starts at the observed
                  state and approaches the steady value, never jumps to it
  7  states       predicted components are labelled simulated
"""

import copy
import sys
from datetime import datetime, timedelta

import pandas as pd

from dtwin.api.engine import get_twin
from dtwin.physics.thermal import hotspot_rise_ultimate, top_oil_rise_ultimate
from dtwin.twin import components as TC
from dtwin.twin.simulate import simulate

R = []


def check(name, ok, detail=""):
    R.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


def fingerprint(tw):
    return (int(pd.util.hash_pandas_object(tw.rows.astype(str)).sum()),
            int(pd.util.hash_pandas_object(tw.df).sum()), repr(tw.np_))


a, b = get_twin("showcase"), get_twin("healthy")
fa, fb = fingerprint(a), fingerprint(b)
i = a.locate("2026-04-23 15:00")
before_a = TC.assess(TC.from_twin(a, i))
before_b = TC.assess(TC.from_twin(b, 1000))
r = simulate(a, i, {"load_pct": 130, "ambient_c": 45, "cooling_effectiveness": 0.7, "winding_r_mult": 1.5})
check("isolation: observed rows, data and nameplate unchanged by a simulation", fingerprint(a) == fa)
check("assets: simulating one asset leaves another's data and states unchanged",
      fingerprint(b) == fb and TC.assess(TC.from_twin(b, 1000)) == before_b)
check("observed component states unchanged after simulating", TC.assess(TC.from_twin(a, i)) == before_a)

# ---- live buffer --------------------------------------------------------------------
from dtwin.live.sim import LiveSimulator
from dtwin.live.store import LiveStore
store = LiveStore()
sim = LiveSimulator(start=datetime(2026, 5, 1) - timedelta(hours=48), seed=5)
store.add([dict(sim.step(), source="simulator") for _ in range(192)], prewarm=True)
frame_before = store.frame().copy()
simulate(store.twin, len(store.twin.rows) - 1, {"load_pct": 140})
check("live: simulating on the live buffer leaves the buffer untouched", store.frame().equals(frame_before))

# ---- identity with the C57.91 K model -------------------------------------------------
h = get_twin("healthy")
j = h.locate("2026-04-10 14:00")
base = simulate(h, j)
np_ = h.np_
K = base["inputs"]["baseline"]["load_pct"] / 100
amb = base["inputs"]["baseline"]["ambient_c"]
worst = max(h.df[c].iloc[j] for c in ("IR", "IY", "IB")) / (base["inputs"]["baseline"]["load_pct"] / 100 * np_.rated_current_a)
want_oil = amb + top_oil_rise_ultimate(K, np_)
got_oil = base["baseline"]["point"]["steady_top_oil_c"]
check("identity: no edits -> steady top oil equals C57.91 at this load (within circuit second-order effects)",
      abs(got_oil - want_oil) < 0.3, f"{got_oil:.2f} vs {want_oil:.2f} °C")
check("no edits -> simulated trajectory identical to baseline", base["simulated"]["series"] == base["baseline"]["series"])

# ---- physics ----------------------------------------------------------------------------
up = simulate(h, j, {"load_pct": base["inputs"]["baseline"]["load_pct"] * 1.3})
bp, sp = up["baseline"]["point"], up["simulated"]["point"]
check("+30 % load: copper loss x ~1.69 and hot-spot rises",
      abs(sp["p_cu_w"] / bp["p_cu_w"] - 1.69) < 0.03 and sp["steady_hotspot_c"] > bp["steady_hotspot_c"],
      f"P_cu {bp['p_cu_w']:.0f} -> {sp['p_cu_w']:.0f} W (x{sp['p_cu_w'] / bp['p_cu_w']:.3f}), "
      f"hot-spot {bp['steady_hotspot_c']:.1f} -> {sp['steady_hotspot_c']:.1f} °C")
hot = simulate(h, j, {"ambient_c": amb + 10})
check("+10 K ambient: steady top oil and hot-spot shift by 10 K",
      abs(hot["simulated"]["point"]["steady_top_oil_c"] - hot["baseline"]["point"]["steady_top_oil_c"] - 10) < 1e-6
      and abs(hot["simulated"]["point"]["steady_hotspot_c"] - hot["baseline"]["point"]["steady_hotspot_c"] - 10) < 1e-6)
cool = simulate(h, j, {"cooling_effectiveness": 0.7})
check("cooling x0.7: oil rises, core loss unchanged",
      cool["simulated"]["point"]["steady_top_oil_c"] > cool["baseline"]["point"]["steady_top_oil_c"]
      and abs(cool["simulated"]["point"]["p_core_w"] - cool["baseline"]["point"]["p_core_w"]) < 1e-6)
v = simulate(h, j, {"input_voltage_pu": 1.10})
check("+10 % voltage: core loss ~ x1.21 (V^2 / Rc), magnetising current up",
      abs(v["simulated"]["point"]["p_core_w"] / v["baseline"]["point"]["p_core_w"]
          - (1.10 / base["inputs"]["baseline"]["input_voltage_pu"]) ** 2) < 0.03
      and v["simulated"]["point"]["Im_a"] > v["baseline"]["point"]["Im_a"])

# ---- dynamics ---------------------------------------------------------------------------
s = up["simulated"]["series"]
obs_oil = float(h.df.oil_temp.iloc[j])
check("stepped in time: starts at the observed oil, approaches steady, never jumps",
      abs(s["top_oil_c"][0] - obs_oil) < 0.01 and abs(s["top_oil_c"][1] - s["top_oil_c"][0]) < 3
      and abs(s["top_oil_c"][-1] - sp["steady_top_oil_c"]) < abs(s["top_oil_c"][0] - sp["steady_top_oil_c"]),
      f"{s['top_oil_c'][0]:.1f} -> {s['top_oil_c'][1]:.1f} (10 min) -> {s['top_oil_c'][-1]:.1f} °C at 24 h; steady {sp['steady_top_oil_c']:.1f}")

# ---- states ---------------------------------------------------------------------------------
check("predicted component states are labelled simulated",
      r["components"]["simulated"] and all(c["reason"].startswith("Simulated") for c in r["components"]["components"]))

print(f"\n  {sum(R)} of {len(R)} simulate checks passed")
sys.exit(0 if all(R) else 1)

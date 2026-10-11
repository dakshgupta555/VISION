"""
What-if (Simulate tab): checks on what the tab leans on.

    python check_whatif.py

  1  deterministic: the same inputs twice give identical JSON
  2  no edit returns the baseline: simulated point equals baseline point
  3  load up -> copper loss x(k^2), steady hot-spot and ageing up
  4  ambient +10 K -> every steady temperature +10 K exactly, rises unchanged
  5  Rth x1.5 (effectiveness 1/1.5) -> steady oil rise x1.5, hot-spot up
  6  the observed twin is untouched by running a what-if (/state identical)
  7  silica-gel condition is not an input of the simulation: it cannot reach hot-spot
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from dtwin.api import app as A                       # noqa: E402
from dtwin.twin import simulate as S                 # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)


tw = A._twin("showcase")
i = 1500
sim = lambda inp=None: S.simulate(tw, i, inp or {})   # noqa: E731
j = lambda x: json.dumps(x, sort_keys=True, default=str)   # noqa: E731

a, b = sim({"load_pct": 120}), sim({"load_pct": 120})
check("1 deterministic", j(a) == j(b))

z = sim()
check("2 no edit: simulated point equals baseline point", j(z["baseline"]["point"]) == j(z["simulated"]["point"]))

base = z["baseline"]["point"]
load0 = z["inputs"]["baseline"]["load_pct"]
up = sim({"load_pct": load0 * 1.2})["simulated"]["point"]
ratio = up["p_cu_w"] / base["p_cu_w"]
check("3 load +20 %: copper loss ~ x1.44, hot-spot and ageing up",
      abs(ratio - 1.44) < 0.03 and up["steady_hotspot_c"] > base["steady_hotspot_c"] and up["steady_faa"] > base["steady_faa"],
      f"Pcu x{ratio:.3f}")

amb0 = z["inputs"]["baseline"]["ambient_c"]
hot = sim({"ambient_c": amb0 + 10})["simulated"]["point"]
check("4 ambient +10 K shifts steady temperatures by exactly 10 K",
      abs(hot["steady_hotspot_c"] - base["steady_hotspot_c"] - 10) < 0.02
      and abs(hot["steady_top_oil_c"] - base["steady_top_oil_c"] - 10) < 0.02)

eff0 = z["inputs"]["baseline"]["cooling_effectiveness"]
r15 = sim({"cooling_effectiveness": eff0 / 1.5})["simulated"]["point"]
rise_b = base["steady_top_oil_c"] - amb0
rise_s = r15["steady_top_oil_c"] - amb0
check("5 Rth x1.5: steady oil rise x1.5, hot-spot up", abs(rise_s / rise_b - 1.5) < 0.01
      and r15["steady_hotspot_c"] > base["steady_hotspot_c"], f"x{rise_s / rise_b:.3f}")

before = j(A.state("showcase", str(i)))
sim({"load_pct": 150, "ambient_c": 45})
check("6 observed /state untouched by a what-if", j(A.state("showcase", str(i))) == before)

check("7 silica gel is not a simulation input", "silica" not in " ".join(S.INPUTS) and
      all(math.isfinite(v) for v in [base["steady_hotspot_c"]]))
print(f"\n{sum(results)} of {len(results)} checks passed")
sys.exit(0 if all(results) else 1)

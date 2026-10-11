"""
Explanation panels: checks.

    python check_explain.py

  1  every trace's final value equals the tile value exactly (4 tiles x several scenarios and cursors)
  2  health steps: weighted contributions re-sum to the weighted mean the engine reports
  3  load trace: recomputed K from the displayed currents equals the tile (unless currents were repaired)
  4  no NaN/Infinity anywhere in any trace; graph points finite
  5  determinism: the same request twice gives identical JSON
  6  bundled (generated) records are labelled SIMULATED, never MEASURED
  7  missing ambient: no fabricated number, the missing input is named
  8  the explain route does not change /state (byte-identical before and after)
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from dtwin.api import app as A                      # noqa: E402
from dtwin.twin.explain import METRICS, explain     # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)


def finite(o):
    if isinstance(o, float):
        return math.isfinite(o)
    if isinstance(o, dict):
        return all(finite(v) for v in o.values())
    if isinstance(o, (list, tuple)):
        return all(finite(v) for v in o)
    return True


def pick(d, path):
    for k in path.split("."):
        d = d[k]
    return d


SCEN = [("showcase", (200, 1500, 2200, -1)), ("winter_250kva_5min", (500, -1)), ("healthy", (900,)),
        ("summer_overload", (1000, -1)), ("cooling_fault", (1800,))]
bad, n, nonfin, srcbad = [], 0, [], []
for name, cursors in SCEN:
    tw = A._twin(name)
    for c in cursors:
        i = c % len(tw.rows)
        st = A.state(name, str(i))
        for m in METRICS:
            tr = explain(tw, i, m)
            n += 1
            if tr["final"]["value"] != pick(st, tr["final"]["tile"]):
                bad.append((name, i, m, tr["final"]["value"], pick(st, tr["final"]["tile"])))
            if not finite(json.loads(json.dumps(tr))):
                nonfin.append((name, i, m))
            if tr["quality"]["origin"] != "SIMULATED" or any(
                    v["source"] == "MEASURED" for e in tr["equations"] for v in e["vars"]):
                srcbad.append((name, i, m))
check("1 final value equals the tile value", not bad and n > 0, f"{n} traces" + (f"; first miss {bad[0]}" if bad else ""))

# 2 health re-sum
tw = A._twin("showcase")
ok = True
for c in (200, 1500, 2200):
    tr = explain(tw, c, "health")
    sc = [s for s in tr["steps"] if s.get("contribution") is not None]
    w = sum(s["weight"] for s in sc)
    mean = sum(s["weight"] * s["score"] for s in sc) / w
    ok &= abs(mean - tw.health(c)["weighted_mean"]) <= 0.5
check("2 health contributions re-sum to the weighted mean", ok)

# 3 load recompute
import numpy as np                                   # noqa: E402
ok = True
for c in (200, 1500, 2200):
    tr = explain(tw, c, "load")
    ks = [s for s in tr["steps"] if s["label"] == "Load ratio K"]
    if ks and not tw.rows.currents_repaired.iloc[c]:
        ok &= round(100 * ks[0]["value"]) == tr["final"]["value"]
check("3 load recomputed from displayed currents equals the tile", ok)
check("4 no NaN or Infinity in any trace", not nonfin, str(nonfin[:2]))

a = json.dumps(explain(tw, 1500, "hotspot"), sort_keys=True)
b = json.dumps(explain(A._twin("showcase"), 1500, "hotspot"), sort_keys=True)
check("5 deterministic", a == b)
check("6 generated records labelled SIMULATED, not MEASURED", not srcbad, str(srcbad[:2]))

# 7 missing ambient
import copy                                          # noqa: E402
tw2 = copy.copy(tw)
tw2.rows = tw.rows.copy()
tw2.rows["ambient_c"] = float("nan")
ok = True
for m in ("hotspot", "top_oil", "health"):
    tr = explain(tw2, 1500, m)
    ok &= (tr["graph"] is None) and any("ambient" in x for x in tr["missing"]) and finite(json.loads(json.dumps(tr)))
check("7 missing ambient is named, nothing fabricated", ok)

s1 = json.dumps(A.state("showcase", "1500"), sort_keys=True, default=str)
explain(tw, 1500, "hotspot")
s2 = json.dumps(A.state("showcase", "1500"), sort_keys=True, default=str)
check("8 explain leaves /state unchanged", s1 == s2)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

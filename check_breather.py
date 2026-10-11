"""
Silica-gel breather: checks.

    python check_breather.py

  1  default is "unknown", grey, source ASSUMED
  2  all four states resolve; an unknown name is refused (ValueError / HTTP 422)
  3  the condition never reaches the engine: /state, /components, /simulate identical for every condition
  4  chain: moisture status says "not calculated" (no number invented); hot-spot and ageing "unaffected"
  5  colours: valid, distinct, and the convention is labelled illustrative and says real gels differ
  6  the 3D model has the part: components.yaml id, focus point
  7  no quantitative coupling is on by default
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from dtwin.api import app as A                         # noqa: E402
from dtwin.twin import breather as B                   # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)


d = B.describe(None)
check("1 default unknown, grey, ASSUMED",
      d["condition"] == "unknown" and d["colour"] == "#a3aaa6" and d["source"]["kind"] == "ASSUMED"
      and d["chain"][0]["value"].startswith("Unknown"))

ok = all(B.describe(c)["condition"] == c for c in B.STATES)
try:
    B.describe("blue")
    ok = False
except ValueError:
    pass
code = None
try:
    A.breather("wet")
except Exception as e:                                  # HTTPException
    code = getattr(e, "status_code", None)
check("2 four states resolve, bad name refused", ok and code == 422, f"HTTP {code}")


def snapshot():
    return json.dumps({"state": A.state("showcase", "1500"), "comp": A.components("showcase", "1500"),
                       "series": A.series("showcase", "1500"), "whatif": A.whatif("showcase", "1500")},
                      sort_keys=True, default=str)


base = snapshot()
same = True
for c in B.STATES:
    B.describe(c)
    A.breather(c)
    same &= snapshot() == base
check("3 engine outputs identical for every condition", same)

ok = True
for c in B.STATES:
    ch = {x["key"]: x for x in B.describe(c)["chain"]}
    ok &= ch["insulation_moisture_status"]["value"].startswith("not calculated")
    ok &= ch["thermal_hotspot_temperature"]["value"] == "unaffected"
    ok &= ch["insulation_ageing_indicator"]["value"] == "unaffected"
    ok &= B.describe(c)["health"]["changed"] is False
check("4 no moisture number invented; hot-spot, ageing, health unaffected", ok)

cols = [B.describe(c)["colour"] for c in B.STATES]
conv = B.describe("effective")["convention"]
check("5 colours valid and distinct; convention labelled illustrative",
      all(re.fullmatch(r"#[0-9a-fA-F]{6}", x) for x in cols) and len(set(cols)) == 4
      and "Illustrative indicator convention" in conv and "depend" in conv.lower())

import yaml                                             # noqa: E402
comp = yaml.safe_load(open(ROOT / "config" / "components.yaml"))
ids = [c["id"] for c in comp["components"]]
focus = (ROOT / "dashboard" / "src" / "twin" / "focus.js").read_text()
check("6 breather part in components.yaml and the focus table", "breather" in ids and "breather:" in focus)

check("7 no quantitative coupling by default", not any("coupling" in k for k in B.config()))
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

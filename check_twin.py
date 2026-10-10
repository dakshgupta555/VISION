"""
3D twin component-state rules, over the whole showcase record.

    python check_twin.py

  1  unmonitored components are never green, anywhere in the record
  2  every component carries a reason, and red/yellow ones carry a rule
  3  an instrument fault is never painted as an asset fault (red/yellow)
  4  stale data turns every assessed component grey, with the reason
  5  the known moments: cooling -> radiators red; probe offset -> probe
     violet, windings not red; overload -> windings red; IB dead -> B-phase
     windings grey, monitor violet
  6  the per-phase hot-spot shown matches the C57.91 gradient on top oil
"""

import sys

from dtwin.api.engine import get_twin
from dtwin.physics.thermal import hotspot_rise_ultimate
from dtwin.twin import components as TC

R = []


def check(name, ok, detail=""):
    R.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


tw = get_twin("showcase")
unmon = {c["id"] for c in TC.config()["components"] if c["assess"] in ("unmonitored", "conservator")}
bad_green, no_reason, no_rule, sensor_red = [], [], [], []
for i in range(0, len(tw.rows), 40):
    a = TC.assess(TC.from_twin(tw, i))
    for c in a["components"]:
        if c["id"] in unmon and c["state"] == "green":
            bad_green.append((i, c["id"]))
        if not c["reason"]:
            no_reason.append((i, c["id"]))
        if c["state"] in ("red", "yellow") and c["id"] not in unmon and not c["rule"]:
            no_rule.append((i, c["id"]))
        if c["id"] in ("oil_pocket", "monitor_box") and c["state"] in ("red", "yellow"):
            sensor_red.append((i, c["id"]))
n = len(range(0, len(tw.rows), 40))
check("unmonitored components never green", not bad_green, f"{n} instants x {len(unmon)} components; {bad_green[:3]}")
check("every component has a reason; every red/yellow assessed one has a rule", not no_reason and not no_rule,
      f"{no_reason[:2]} {no_rule[:2]}")
check("instrument components never shown as asset faults", not sensor_red, str(sensor_red[:3]))

ev = TC.from_twin(tw, tw.locate("2026-04-23 15:00"), {"stale": True, "reason": "no recent data: test"})
a = TC.assess(ev)
check("stale: every component grey, with the reason", all(c["state"] == "grey" for c in a["components"])
      and all("No recent data" in c["reason"] for c in a["components"] if c["id"] not in unmon),
      f"counts {a['counts']}")


def at(t):
    return {c["id"]: c for c in TC.assess(TC.from_twin(tw, tw.locate(t)))["components"]}


c = at("2026-04-23 15:00")
check("cooling alarm: radiators red, windings not red", c["radiators"]["state"] == "red"
      and all(c[f"lv_winding_{p}"]["state"] != "red" for p in "ryb"))
c = at("2026-04-17 12:00")
check("probe offset: probe violet, no winding red", c["oil_pocket"]["state"] == "sensor"
      and all(c[f"{s}_winding_{p}"]["state"] != "red" for s in ("lv", "hv") for p in "ryb"),
      c["oil_pocket"]["reason"][:80])
c = at("2026-04-14 17:00")
check("overload: all windings red", all(c[f"{s}_winding_{p}"]["state"] == "red" for s in ("lv", "hv") for p in "ryb"))
c = at("2026-04-28 03:00")
check("IB dead: B-phase windings grey, monitor violet", c["lv_winding_b"]["state"] == "grey"
      and c["hv_winding_b"]["state"] == "grey" and c["monitor_box"]["state"] == "sensor")

i = tw.locate("2026-04-14 17:00")
w = at("2026-04-14 17:00")["lv_winding_r"]
k = tw.df.IR.iloc[i] / tw.np_.rated_current_a
want = tw.df.oil_temp.iloc[i] + hotspot_rise_ultimate(k, tw.np_)
check("winding hot-spot = measured top oil + C57.91 gradient", abs(w["hotspot_c"] - want) < 0.06,
      f"{w['hotspot_c']} vs {want:.2f} C")

# ---- health index cap and the physical limit -------------------------------------------------
hl = get_twin("healthy")
binds = [i for i in range(0, len(hl.rows), 8) if hl.health(i)["capped_by"]]
check("health cap never binds on the healthy record", not binds, f"{len(binds)} capped instants")
so = get_twin("summer_overload")
hot = so.rows.hotspot_c.idxmax()
h = so.health(so.locate(hot))
check("extreme hot-spot drives the index near 0 (index <= worst of ageing, loading + 10)",
      so.rows.hotspot_c.max() > 140 and h["index"] <= 10 and h["weighted_mean"] >= 50,
      f"hot-spot {so.rows.hotspot_c.max():.0f} C: index {h['index']}, weighted mean {h['weighted_mean']}")
st = so.state(so.locate(hot))
check("above 140 C: 'protection would trip' shown and no ageing or cost accrues",
      st["physical_limit"]["beyond"] and "protection would trip" in st["physical_limit"]["message"]
      and so.rows.aged_h[so.rows.beyond_trip].sum() == 0 and st["life"]["since_start"]["beyond_trip_h"] > 0,
      f"{st['life']['since_start']['beyond_trip_h']} h above 140 C not counted")

print(f"\n  {sum(R)} of {len(R)} twin checks passed")
sys.exit(0 if all(R) else 1)

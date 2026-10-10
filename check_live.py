"""
Live stream checks: a scripted high-speed run through the real store and
analytics (no HTTP, so it runs in seconds).

    python check_live.py

  1  prewarm      48 h of history makes the 24 h fit and detectors ready
  2  healthy      no alarm on healthy live data after prewarm
  3  blind        a fault in the injection list that the physics never applies
                  produces no alarm: the analytics are not told
  4  cooling      injected cooling degradation -> ASSET (cooling); time reported
  5  drift        injected oil probe drift -> SENSOR (temperature offset), never
                  ASSET; time reported
  6  stale        no sample for 3 periods -> stale, with the reason; a missing
                  channel inside a fresh sample is a dead channel, not stale
  7  contract     a sample with an unknown source is rejected
"""

import inspect
import sys
from datetime import datetime, timedelta

from dtwin.live.sim import LiveSimulator
from dtwin.live.store import LiveStore, analyse

RES = []


def check(name, ok, detail=""):
    RES.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def setup(seed):
    clk = Clock()
    store = LiveStore(clock=clk)
    sim = LiveSimulator(start=datetime(2026, 5, 10, 0, 0) - timedelta(hours=48), seed=seed)
    store.add([dict(sim.step(), source="simulator") for _ in range(4 * 48)], prewarm=True)
    return store, sim, clk


def run(store, sim, clk, hours, apply_faults=True, stop=None):
    """Step the live loop; returns (first time stop() was true, hours after start)."""
    t0 = sim.t
    for _ in range(int(hours * 4)):
        if apply_faults:
            sim.set_faults(store.sim_config()["faults"])
        clk.t += 1.5
        store.add([dict(sim.step(), source="simulator")], period_s=1.5)
        if stop and stop(store.twin):
            return sim.t, (sim.t - t0).total_seconds() / 3600
    return None, None


def last(tw, col):
    return tw.rows[col].iloc[-1]


def main():
    # ---- 1-2 prewarm and healthy -------------------------------------------------
    store, sim, clk = setup(31)
    tw = store.twin
    check("prewarm: 48 h of history, 24 h fit ready at the first live sample",
          tw is not None and tw.rows.slope.notna().iloc[-1] and store.freshness()["prewarm_until"] is not None,
          f"{len(tw.rows)} samples, slope {last(tw, 'slope'):.3f}, analysis {store.analysis_ms} ms")
    hit, _ = run(store, sim, clk, 48, stop=lambda t: bool(t.rows.asset_alarm.iloc[-1] or t.rows.sensor_alarm.iloc[-1]))
    check("healthy: no alarm over 48 h of live data", hit is None, f"analysis {store.analysis_ms} ms per sample")

    # ---- 3 the analytics are blind to the injection list --------------------------
    params = list(inspect.signature(analyse).parameters)
    store.inject("cooling_degradation", 0.4, ramp_h=6)
    hit, _ = run(store, sim, clk, 36, apply_faults=False,
                 stop=lambda t: last(t, "thermal_status") == "asset_cooling")
    check("blind: a listed but unapplied fault raises nothing; analyse() takes samples only",
          hit is None and "faults" not in params, f"analyse{tuple(params)}")
    store.clear()

    # ---- 4 cooling ----------------------------------------------------------------
    store, sim, clk = setup(32)
    f = store.inject("cooling_degradation", 0.30, ramp_h=24)
    t_hit, h = run(store, sim, clk, 96, stop=lambda t: last(t, "thermal_status") == "asset_cooling")
    fam = None if t_hit is None else ("asset" if store.twin.rows.asset_alarm.iloc[-1] else "?")
    check("cooling degradation (30 %, 24 h ramp) -> ASSET", t_hit is not None and fam == "asset",
          f"detected {h:.1f} sim-h after injection, at {100 * min(h / 24, 1) * 0.30:.0f} % extra rise "
          f"({min(h / 24, 1) * 100:.0f} % of final severity)" if t_hit else "not detected in 96 h")
    wrong = (store.twin.rows.thermal_status == "sensor_temp_offset").iloc[-int(4 * (h or 0)):].any() if h else True
    check("cooling: never called a sensor offset on the way", not wrong)

    # ---- 5 oil probe drift --------------------------------------------------------
    store, sim, clk = setup(33)
    store.inject("sensor_drift", 5.0, ramp_h=24, channel="oil_temp")
    seen_asset = []
    t_hit, h = run(store, sim, clk, 96, stop=lambda t: (seen_asset.append(last(t, "thermal_status") == "asset_cooling")
                                                       or last(t, "thermal_status") == "sensor_temp_offset"))
    check("oil probe drift (5 K, 24 h ramp) -> SENSOR, never ASSET",
          t_hit is not None and not any(seen_asset) and store.twin.rows.sensor_alarm.iloc[-1],
          f"detected {h:.1f} sim-h after injection ({min(h / 24, 1) * 5:.1f} K of drift), "
          f"suspect: {store.twin.rows.suspect_channels.iloc[-1]}" if t_hit else "not detected in 96 h")

    # ---- 6 stale vs missing -------------------------------------------------------------
    fr = store.freshness()
    clk.t += 3.5 * 1.5
    st = store.freshness()
    check("stale: no sample for 3 periods -> stale, with the reason",
          not fr["stale"] and st["stale"] and "no recent data" in st["reason"], st["reason"])
    s = dict(sim.step(), source="simulator")
    s["IB"] = None
    clk.t += 1.5
    store.add([s], period_s=1.5)
    fr2 = store.freshness()
    check("missing channel in a fresh sample is a dead channel, not stale",
          not fr2["stale"] and store.twin.rows.ch_IB.iloc[-1] == "dead", f"ch_IB = {store.twin.rows.ch_IB.iloc[-1]}")

    # ---- 7 contract ------------------------------------------------------------------------
    try:
        store.add([dict(sim.step(), source="guess")])
        check("contract: unknown source rejected", False)
    except ValueError as e:
        check("contract: unknown source rejected", True, str(e))

    print(f"\n  {sum(RES)} of {len(RES)} live checks passed")
    return 0 if all(RES) else 1


if __name__ == "__main__":
    sys.exit(main())

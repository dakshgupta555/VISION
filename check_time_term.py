"""
Regression test for the drift term in the thermal fit.

    measured_rise = slope * predicted_rise + intercept + drift * t

The time term exists so a drifting probe lands in drift/intercept instead of
aliasing into the slope. The risk is the reverse: a genuine cooling fault also
ramps in time, so the time term could absorb real degradation and leave it
looking like an instrument. The slower the ramp, the more the fault looks like
a drift, so the slowest ramp is the case most at risk.

Each case must be called ASSET (asset_cooling) after detection and never a
temperature-sensor offset. Exits non-zero on failure.

    python check_time_term.py
"""

import sys

import numpy as np

from dtwin.physics.nameplate import preset
from dtwin.simulate.generator import TransformerSimulator, SimConfig
from dtwin.simulate import faults as F
from dtwin.analytics.validation import validate

NP = preset(100)
CASES = [(24, 3001), (96, 3002), (240, 3003)]      # ramp hours, fresh seed
SEVERITY = 0.30

failed = False
print(f"  {'ramp':>6} {'detected':>9} {'latency':>8} {'sev@det':>8} "
      f"{'asset post-det':>15} {'sensor offset':>14} {'slope':>6} {'drift K/d':>10}")
for ramp_h, seed in CASES:
    sim = TransformerSimulator(NP, SimConfig(days=30, seed=seed))
    df, truth = sim.generate(custom_faults=[
        F.cooling_degradation(start_h=4 * 24, severity=SEVERITY, ramp_h=ramp_h)])
    rows, fits, _ = validate(df.drop(columns="transformer_id"), NP)
    rows = rows.reindex(truth.index)
    fault = (truth.fault_label != "none").values
    asset = (rows.thermal_status == "asset_cooling").values
    offset = (rows.thermal_status == "sensor_temp_offset").values

    det = np.where(asset & fault)[0]
    first = det[0] if len(det) else None
    post = np.arange(first, len(rows))[fault[first:]] if first is not None else []
    cf = truth.true_cooling_factor.values
    sev = 100 * (cf[first] - 1) / SEVERITY if first is not None else np.nan
    lat = (first - np.argmax(fault)) * 0.25 if first is not None else np.nan
    asset_post = 100 * asset[post].mean() if len(post) else 0.0
    offset_any = 100 * offset[fault].mean()
    sl = rows.slope.values[fault]
    dr = rows.drift_k_per_day.values[fault]
    print(f"  {ramp_h:>4} h {str(first is not None):>9} {lat:>6.1f} h {sev:>7.1f}% "
          f"{asset_post:>14.1f}% {offset_any:>13.1f}% {np.nanmedian(sl):>6.2f} "
          f"{np.nanmedian(dr):>+10.2f}")
    if first is None or asset_post < 95.0 or offset_any > 0.0:
        failed = True

print("\n  PASS - every ramp called asset, none called sensor" if not failed else
      "\n  FAIL - a cooling fault was missed or called a sensor offset")
sys.exit(1 if failed else 0)

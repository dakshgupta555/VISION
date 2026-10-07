"""
Generate the held-out validation set.

Every seed, severity, ramp rate, rating, cadence, season and load profile here
was chosen without reference to detector results and differs from what the
detector was developed against (seeds 42, 7, 11; data/*.csv). The detector is
frozen before this set is scored, and it is scored once.

    python make_heldout.py      # writes data/heldout/*.csv and manifest.json
"""

import json
import os
from dataclasses import asdict

from dtwin.physics.nameplate import preset
from dtwin.simulate.generator import TransformerSimulator, SimConfig
from dtwin.simulate import faults as F

D = 24  # hours per day

HELDOUT = {
    # fast, mild cooling fault at a 10-minute cadence
    "ho_fast_mild_cooling": dict(
        rating_kva=100, cfg=dict(seed=2001, sample_minutes=10, season="spring",
                                 load_profile="residential"),
        faults=[F.cooling_degradation(start_h=10 * D, end_h=20 * D, severity=0.18, ramp_h=24)]),

    # asset and sensor fault overlapping: probe drifts while cooling degrades
    "ho_overlap_cooling_drift": dict(
        rating_kva=100, cfg=dict(seed=2002, season="summer", load_profile="mixed"),
        faults=[F.cooling_degradation(start_h=8 * D, end_h=22 * D, severity=0.45, ramp_h=140),
                F.sensor_drift(start_h=12 * D, end_h=16 * D, channel="oil_temp",
                               severity=3.5, ramp_h=30)]),

    # small unit, slow oil loss, stuck oil probe, dead R-phase CT
    "ho_63kva_oilloss": dict(
        rating_kva=63, cfg=dict(seed=2003, season="monsoon", load_profile="commercial"),
        faults=[F.oil_loss(start_h=6 * D, severity=0.20, ramp_h=200),
                F.sensor_stuck(start_h=18 * D + 10, end_h=18 * D + 16, channel="oil_temp"),
                F.sensor_dead(start_h=25 * D, end_h=25 * D + 3, channel="IR")]),

    # 160 kVA at 30-minute cadence: unbalance, positive ambient offset, overload
    "ho_160kva_30min": dict(
        rating_kva=160, cfg=dict(seed=2004, sample_minutes=30, season="winter",
                                 load_profile="agricultural"),
        faults=[F.unbalance_growth(start_h=5 * D, end_h=12 * D, severity=0.14, ramp_h=36,
                                   channel="IB"),
                F.sensor_bias(start_h=16 * D, end_h=19 * D, channel="ambient_temp",
                              severity=2.5),
                F.overload(start_h=22 * D + 17, end_h=22 * D + 21, severity=1.10)]),

    # ambient probe drifting low, dead VT, severe fast cooling fault
    "ho_drift_ambient_vt": dict(
        rating_kva=100, cfg=dict(seed=2005, season="summer", peak_load_pu=0.85),
        faults=[F.sensor_drift(start_h=4 * D, end_h=10 * D, channel="ambient_temp",
                               severity=-4.5, ramp_h=90),
                F.sensor_dead(start_h=14 * D, end_h=14 * D + 6, channel="VR"),
                F.cooling_degradation(start_h=18 * D, end_h=26 * D, severity=0.60, ramp_h=60)]),

    # no faults at all: false-alarm baseline on a 25 kVA pole-mount
    "ho_healthy_25kva": dict(
        rating_kva=25, cfg=dict(seed=2006, season="monsoon", load_profile="commercial"),
        faults=[]),
}


def main(out="data/heldout"):
    os.makedirs(out, exist_ok=True)
    manifest = {}
    for name, spec in HELDOUT.items():
        sim = TransformerSimulator(preset(spec["rating_kva"]), SimConfig(days=30, **spec["cfg"]))
        df, truth = sim.generate(custom_faults=spec["faults"])
        df.to_csv(f"{out}/{name}.csv")
        truth.to_csv(f"{out}/{name}_truth.csv")
        manifest[name] = {"rating_kva": spec["rating_kva"], "config": spec["cfg"],
                          "faults": [asdict(f) for f in spec["faults"]]}
        print(f"  {name:<28} {len(df):5d} rows  "
              f"{100 * (truth.fault_label == 'none').mean():5.1f}% clean")
    json.dump(manifest, open(f"{out}/manifest.json", "w"), indent=2)


if __name__ == "__main__":
    main()

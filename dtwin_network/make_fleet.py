"""
Regenerate config_network/fleet.yaml: one synthetic district, illustrative only.

    python -m dtwin_network.make_fleet            # writes config_network/fleet.yaml

Every position, rating, cost and metadata value here is invented with a fixed
seed so the file is reproducible. None of it is CPRI infrastructure or
procurement data. Edit the YAML directly for a real fleet; this script is only
how the demo file was made.
"""

from pathlib import Path

import numpy as np
import yaml

OUT = Path(__file__).resolve().parents[1] / "config_network" / "fleet.yaml"
SEED = 20261011
MANUFACTURERS = ["Mfr-A", "Mfr-B", "Mfr-C", "Mfr-D"]       # synthetic, not real brands

# Existing gateways and the monitored units on them. DT-001 mirrors the
# single-transformer twin: 100 kVA, load seed 7, peak 0.75 pu, no ambient offset.
GATEWAYS = [
    {"id": "GW-A", "name": "Substation North (existing)", "xy_km": [3.6, 6.2], "capacity_nodes": 8},
    {"id": "GW-B", "name": "Ward office (existing)", "xy_km": [8.4, 6.8], "capacity_nodes": 8},
    {"id": "GW-C", "name": "Pumping station (existing)", "xy_km": [10.8, 2.8], "capacity_nodes": 6},
]
MONITORED = [  # id, kVA, gateway, xy, load seed, peak pu, ambient offset K, manufacturer
    ("DT-001", 100, "GW-A", [3.1, 5.4], 7, 0.75, 0.0, "Mfr-A"),
    ("DT-002", 63, "GW-A", [2.2, 7.1], 11, 0.70, -0.5, "Mfr-B"),
    ("DT-003", 250, "GW-A", [4.7, 7.3], 12, 0.80, 0.8, "Mfr-A"),
    ("DT-004", 100, "GW-A", [4.4, 4.9], 13, 0.68, 0.3, "Mfr-C"),
    ("DT-005", 63, "GW-A", [1.8, 5.6], 14, 0.72, -1.0, "Mfr-B"),
    ("DT-006", 250, "GW-B", [7.6, 7.9], 15, 0.78, 1.2, "Mfr-D"),
    ("DT-007", 100, "GW-B", [9.6, 7.4], 16, 0.74, 0.6, "Mfr-A"),
    ("DT-008", 63, "GW-B", [8.9, 5.5], 17, 0.66, 0.4, "Mfr-C"),
    ("DT-009", 100, "GW-B", [7.2, 6.1], 18, 0.71, 0.9, "Mfr-B"),
    ("DT-010", 250, "GW-C", [11.6, 3.6], 19, 0.82, 1.5, "Mfr-D"),
    ("DT-011", 63, "GW-C", [10.1, 2.0], 20, 0.69, 1.1, "Mfr-C"),
    ("DT-012", 100, "GW-C", [11.9, 1.9], 21, 0.73, 1.3, "Mfr-A"),
]
CANDIDATE_GATEWAYS = [
    {"id": "CG-1", "name": "School rooftop (candidate)", "xy_km": [1.6, 2.4]},
    {"id": "CG-2", "name": "Water tank (candidate)", "xy_km": [5.6, 2.2]},
    {"id": "CG-3", "name": "Market building (candidate)", "xy_km": [6.2, 9.4]},
    {"id": "CG-4", "name": "Telecom mast (candidate)", "xy_km": [12.4, 7.8]},
    {"id": "CG-5", "name": "Depot (candidate)", "xy_km": [13.4, 5.0]},
]
# Pockets where unmonitored units cluster: (centre, spread km, how many)
POCKETS = [([3.4, 6.0], 1.4, 7), ([8.6, 6.6], 1.3, 6), ([10.9, 3.0], 1.2, 4),
           ([1.8, 2.6], 1.0, 5), ([5.8, 2.0], 1.1, 5), ([6.0, 9.2], 0.9, 4),
           ([12.6, 7.6], 0.9, 3), ([13.3, 5.2], 0.8, 3), ([0.6, 9.4], 0.3, 2),
           ([14.2, 0.6], 0.3, 1)]
SITE_TYPES = ["residential", "commercial", "agricultural pump", "hospital feeder", "water works", "residential"]


def build():
    rng = np.random.default_rng(SEED)
    assets = []
    for i, (tid, kva, gw, xy, seed, peak, amb, mfr) in enumerate(MONITORED):
        assets.append({
            "id": tid, "monitored": True, "gateway": gw,
            "nameplate": {"rating_kva": kva}, "manufacturer": mfr,
            "xy_km": xy, "coordinates_synthetic": True,
            "sim": {"load_seed": seed, "peak_load_pu": peak, "ambient_offset_c": amb},
            "metadata": {"age_years": int(rng.integers(3, 22)), "peak_load_ratio": peak,
                         "customers": int(rng.integers(40, 420)), "criticality": int(rng.integers(1, 4)),
                         "site": SITE_TYPES[i % len(SITE_TYPES)], "past_failures_5y": int(rng.integers(0, 2))},
            "node_cost_inr": 22000, "node_annual_inr": 1500,
        })
    n = 0
    for centre, spread, k in POCKETS:
        for _ in range(k):
            n += 1
            xy = np.clip(np.array(centre) + rng.normal(0, spread / 2, 2), [0.2, 0.2], [14.6, 9.8])
            kva = int(rng.choice([25, 63, 100, 160, 250], p=[0.2, 0.3, 0.25, 0.15, 0.1]))
            site = str(rng.choice(SITE_TYPES))
            crit = 3 if site in ("hospital feeder", "water works") else int(rng.integers(1, 3))
            assets.append({
                "id": f"DT-{100 + n:03d}", "monitored": False, "gateway": None,
                "nameplate": {"rating_kva": kva}, "manufacturer": str(rng.choice(MANUFACTURERS)),
                "xy_km": [round(float(xy[0]), 2), round(float(xy[1]), 2)], "coordinates_synthetic": True,
                "metadata": {"age_years": int(rng.integers(2, 34)),
                             "peak_load_ratio": round(float(np.clip(rng.normal(0.86, 0.17), 0.4, 1.35)), 2),
                             "customers": int(rng.integers(20, 480)), "criticality": crit, "site": site,
                             "past_failures_5y": int(rng.choice([0, 0, 0, 1, 1, 2, 3]))},
                "node_cost_inr": 22000, "node_annual_inr": 1500,
            })
    return {
        "district": {"name": "Synthetic district, simulated", "extent_km": [15.0, 10.2],
                     "disclaimer": "Simulated network: positions, radio coverage, telemetry and costs are "
                                   "illustrative, not CPRI infrastructure or procurement data."},
        "radio": {"topology": "LoRaWAN star-of-stars: node -> gateway -> backend. No multi-hop mesh; "
                              "gateways forward packets and never average them.",
                  "coverage_radius_km": 3.0, "reporting_interval_min": 15,
                  "stale_after_missed": 2, "offline_after_missed": 6, "node_buffer_packets": 192},
        "costs": {"currency": "INR", "illustrative": True, "node_cost_inr": 22000, "node_annual_inr": 1500,
                  "gateway_cost_inr": 120000, "gateway_annual_inr": 18000, "plan_years": 5},
        "gateways": [{**g, "existing": True, "coordinates_synthetic": True, "coverage_radius_km": 3.0,
                      "cost_inr": 120000, "annual_inr": 18000} for g in GATEWAYS]
                    + [{**g, "existing": False, "coordinates_synthetic": True, "coverage_radius_km": 3.0,
                        "capacity_nodes": 12, "cost_inr": 120000, "annual_inr": 18000} for g in CANDIDATE_GATEWAYS],
        "assets": assets,
    }


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    head = ("# Synthetic district for the GridTwin network dashboard. ILLUSTRATIVE ONLY:\n"
            "# positions, radio coverage, telemetry and costs are not CPRI infrastructure or\n"
            "# procurement data. Regenerate with: python -m dtwin_network.make_fleet\n")
    OUT.write_text(head + yaml.safe_dump(build(), sort_keys=False, width=110))
    print(f"wrote {OUT}")

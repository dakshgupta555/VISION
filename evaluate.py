"""
Score the measurement validation layer against ground truth.

This is the ONLY place a *_truth.csv is read. The validator sees the measured
CSV and nothing else; truth is joined afterwards, purely to count.

    python evaluate.py                 # showcase in detail, all scenarios summarised
    python evaluate.py cooling_fault   # one scenario in detail

For every fault type: event detection, sample detection rate, latency, and
whether the alarm was raised in the right family (asset vs sensor). For clean
samples: false-alarm rate, as a percentage and as alarm episodes per day, with
and without the hangover window after a fault clears.
"""

import sys

import numpy as np
import pandas as pd

from dtwin.physics.nameplate import preset
from dtwin.physics.thermal import ThermalModel
from dtwin.analytics.validation import validate, _runs

pd.set_option("display.width", 200)
head = lambda t: print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)

SCENARIOS = ["showcase", "healthy", "cooling_fault", "summer_overload",
             "sensor_trouble", "unbalance", "oil_loss", "winter_250kva_5min"]
RATING = {"winter_250kva_5min": 250}
HANGOVER_H = 24.0      # the trailing fit window: alarms may outlast a fault this long

# What a correct call looks like for each injected fault kind.
EXPECT = {
    "cooling_degradation": ("asset", lambda r, ch: r.thermal_status == "asset_cooling"),
    "oil_loss":            ("asset", lambda r, ch: r.thermal_status == "asset_cooling"),
    "overload":            ("asset", lambda r, ch: r.overload),
    "unbalance_growth":    ("asset", lambda r, ch: r.elec_status == "asset_unbalance"),
    "sensor_drift":        ("sensor", lambda r, ch: ch in r.suspect_channels.split(",")),
    "sensor_bias":         ("sensor", lambda r, ch: ch in r.suspect_channels.split(",")),
    "sensor_stuck":        ("sensor", lambda r, ch: r[f"ch_{ch}"] == "stuck"),
    "sensor_dead":         ("sensor", lambda r, ch: r[f"ch_{ch}"] == "dead"),
}


def load(name):
    df = pd.read_csv(f"data/{name}.csv", parse_dates=["timestamp"], index_col="timestamp")
    truth = pd.read_csv(f"data/{name}_truth.csv", parse_dates=["timestamp"], index_col="timestamp")
    return df.drop(columns=["transformer_id"], errors="ignore"), truth


def components(truth):
    """Split the ';'-joined labels into (kind, channel) pairs with row masks."""
    out = {}
    for lab in truth.fault_label.unique():
        if lab == "none":
            continue
        for part in lab.split(";"):
            kind, _, ch = part.partition(":")
            key = (kind, ch or None)
            if key not in out:
                out[key] = truth.fault_label.str.split(";").apply(lambda xs: part in xs).values
    return out


def _masked_mean(x, m):
    return float(np.asarray(x)[m].mean()) if np.asarray(m).any() else 0.0


def score(name, verbose=True):
    df, truth = load(name)
    np_ = preset(RATING.get(name, 100))
    rows, fits, meta = validate(df, np_)
    rows = rows.reindex(truth.index)
    dt_h = (truth.index[1] - truth.index[0]).total_seconds() / 3600.0
    any_alarm = (rows.sensor_alarm | rows.asset_alarm | rows.other_alarm).values

    results = []
    for (kind, ch), mask in components(truth).items():
        family, correct = EXPECT[kind]
        wrong_col = "asset_alarm" if family == "sensor" else "sensor_alarm"
        other_truth = "is_asset_fault" if family == "sensor" else "is_sensor_fault"
        hit = np.array([bool(correct(r, ch)) for _, r in rows.iterrows()])
        for s, e in _runs(mask):
            seg = slice(s, e + 1)
            seg_hit = hit[seg]
            first = np.argmax(seg_hit) if seg_hit.any() else None
            fam_ok = rows[f"{family}_alarm"].values[seg]
            results.append({
                "fault": f"{kind}{':' + ch if ch else ''}",
                "family": family,
                "start": truth.index[s],
                "hours": round((e - s + 1) * dt_h, 1),
                "detected": seg_hit.any(),
                "latency_h": round(first * dt_h, 2) if first is not None else np.nan,
                "sample_rate": seg_hit.mean(),
                "family_rate": fam_ok.mean(),
                # temperature offsets may be reported as the oil/ambient pair;
                # this counts only samples that name the faulty channel alone
                "exact_rate": float(np.mean([
                    r.suspect_channels.split(",").count(ch) == 1 and
                    not ({"oil_temp", "ambient_temp"} - {ch}) & set(r.suspect_channels.split(","))
                    for _, r in rows.iloc[seg].iterrows()])) if family == "sensor" else np.nan,
                # ignore rows where a fault of the other family is genuinely
                # present too (e.g. a stuck CT during the cooling fault)
                "wrong_family": _masked_mean(rows[wrong_col].values[seg],
                                             ~truth[other_truth].values[seg]),
            })
    res = pd.DataFrame(results)

    # ---- false alarms ------------------------------------------------------
    clean = (truth.fault_label == "none").values
    faulty = ~clean
    hang = np.zeros(len(truth), bool)
    k = int(round(HANGOVER_H / dt_h))
    for s, e in _runs(faulty):
        hang[e + 1:e + 1 + k] = True
    clean_far = clean & ~hang
    days = len(truth) * dt_h / 24.0

    def episodes(m):
        return len(_runs(any_alarm & m))

    far = {
        "clean_samples": int(clean.sum()),
        "far_pct": 100.0 * any_alarm[clean].mean() if clean.any() else np.nan,
        "far_pct_excl_hangover": 100.0 * any_alarm[clean_far].mean() if clean_far.any() else np.nan,
        "episodes": episodes(clean),
        "episodes_excl_hangover": episodes(clean_far),
        "episodes_per_day_excl_hangover": episodes(clean_far) / days,
    }

    lead = cooling_lead(df, truth, rows, np_, dt_h) if (truth.fault_label.str.contains(
        "cooling|oil_loss")).any() else None

    if verbose:
        report(name, res, far, lead, rows, truth, meta)
    return res, far, lead


def cooling_lead(df, truth, rows, np_, dt_h):
    """Lead time against the moment the fault becomes operationally significant.

    The fault's own contribution to oil temperature is found by running a
    healthy twin on the TRUE load and ambient and subtracting it from the true
    top-oil. Evaluation only - the validator never sees any of this.
    """
    K = truth.true_load_pu.values
    healthy, _ = ThermalModel(np_).run(K, K, truth.true_ambient_c.values, dt_h * 60.0)
    excess = truth.true_top_oil_c.values - healthy
    fault = truth.fault_label.str.contains("cooling|oil_loss").values
    s, e = _runs(fault)[0]
    t0 = truth.index[s]
    alarm = (rows.thermal_status == "asset_cooling").values
    first_alarm = np.argmax(alarm[s:e + 1]) + s if alarm[s:e + 1].any() else None
    out = {"onset": t0, "first_alarm": truth.index[first_alarm] if first_alarm else None}
    for lvl in (3.0, 5.0):
        j = np.where((excess >= lvl) & fault)[0]
        out[f"t_plus{int(lvl)}K"] = truth.index[j[0]] if len(j) else None
    over60 = np.where(fault & (df.oil_temp.values > np_.trip_oil_temp_c))[0]
    out["first_60C"] = truth.index[over60[0]] if len(over60) else None
    out["excess_at_alarm_k"] = float(excess[first_alarm]) if first_alarm else np.nan
    return out


def report(name, res, far, lead, rows, truth, meta):
    head(f"{name.upper()} - per fault event")
    if res.empty:
        print("  no injected faults")
    else:
        show = res.copy()
        show["start"] = show.start.dt.strftime("d%d %H:%M")
        for c in ("sample_rate", "family_rate", "exact_rate", "wrong_family"):
            show[c] = (100 * show[c]).round(1)
        show = show.rename(columns={"sample_rate": "correct%", "family_rate": "family%", "exact_rate": "exact%",
                                    "wrong_family": "wrongfam%"})
        print(show.to_string(index=False))
        print("\n  correct%  = fault samples flagged with the right diagnosis and channel")
        print("  family%   = fault samples flagged in the right family (asset/sensor)")
        print("  exact%    = sensor fault samples naming the faulty channel alone (not the oil|ambient pair)")
        print("  wrongfam% = fault samples also flagged in the WRONG family")

    head(f"{name.upper()} - false alarms on clean samples")
    print(f"  clean samples                     {far['clean_samples']}")
    print(f"  false-alarm rate, all clean       {far['far_pct']:.2f}%  "
          f"({far['episodes']} episodes)")
    print(f"  false-alarm rate, excl {HANGOVER_H:.0f} h after a fault   "
          f"{far['far_pct_excl_hangover']:.2f}%  ({far['episodes_excl_hangover']} episodes, "
          f"{far['episodes_per_day_excl_hangover']:.2f}/day)")
    clean = (truth.fault_label == "none").values
    any_alarm = rows.sensor_alarm | rows.asset_alarm | rows.other_alarm
    if (clean & any_alarm.values).any():
        why = rows[clean & any_alarm.values]
        reasons = (why.thermal_status.where(why.thermal_status.isin(
            ["asset_cooling", "sensor_temp_offset", "thermal_mismatch"]), "")
            + " " + why.elec_status.where(why.elec_status != "ok", "")
            + " " + why.suspect_channels).str.strip()
        print("  what fired on clean samples:")
        print(reasons.value_counts().head(8).to_string())

    if lead:
        head(f"{name.upper()} - cooling fault lead time")
        t0 = lead["onset"]
        fmt = lambda t: "never" if t is None else f"{t:%d %b %H:%M}  (onset + {(t - t0).total_seconds() / 3600:.1f} h)"
        print(f"  fault onset                        {t0:%d %b %H:%M}")
        print(f"  first oil > 60 C in fault window   {fmt(lead['first_60C'])}   <- ambient, not the fault")
        print(f"  first cooling alarm                {fmt(lead['first_alarm'])}"
              f"   fault excess then {lead['excess_at_alarm_k']:.2f} K")
        print(f"  fault adds +3 K                    {fmt(lead['t_plus3K'])}")
        print(f"  fault adds +5 K                    {fmt(lead['t_plus5K'])}")
        if lead["first_alarm"] is not None:
            for k in ("t_plus3K", "t_plus5K"):
                if lead[k] is not None:
                    h = (lead[k] - lead["first_alarm"]).total_seconds() / 3600
                    print(f"  lead over {k[2:]:<6}                   {h:+.1f} h")

    print(f"\n  stuck run length used per channel: {meta['stuck_run_needed']}")


def summary():
    head("ALL SCENARIOS")
    out = []
    for nm in SCENARIOS:
        res, far, lead = score(nm, verbose=False)
        ev = res.detected.sum() if not res.empty else 0
        out.append({
            "scenario": nm,
            "events": f"{ev}/{len(res)}",
            "correct%": round(100 * res.sample_rate.mean(), 1) if not res.empty else np.nan,
            "family%": round(100 * res.family_rate.mean(), 1) if not res.empty else np.nan,
            "FAR%": round(far["far_pct"], 2),
            "FAR% excl hang": round(far["far_pct_excl_hangover"], 2),
            "FA ep/day": round(far["episodes_per_day_excl_hangover"], 2),
            "cool alarm vs +3K h": round((lead["t_plus3K"] - lead["first_alarm"]).total_seconds() / 3600, 1)
                if lead and lead["first_alarm"] is not None and lead["t_plus3K"] is not None else "",
        })
    print(pd.DataFrame(out).to_string(index=False))
    print("\n  correct% / family% are means over events; FAR is over clean samples only.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        score(sys.argv[1])
    else:
        score("showcase")
        summary()

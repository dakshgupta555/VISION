"""
Score the measurement validation layer against ground truth.

This is the ONLY place a *_truth.csv is read. The validator sees the measured
CSV and nothing else; truth is joined afterwards, purely to count.

    python evaluate.py                    # in-sample set (data/), showcase in detail
    python evaluate.py --set heldout      # held-out set (data/heldout/)
    python evaluate.py cooling_fault      # one in-sample scenario in detail

Metrics, per fault event:

  detected        the correct diagnosis appeared at some point in the window
  sev@det         fault severity at first correct detection, as a percentage
                  of the severity the fault eventually reached. This is the
                  primary latency metric because it transfers to data with a
                  different ramp rate; hours do not. "step" for faults that
                  arrive at full severity.
  resid@det       twin residual at detection (trailing 4 h mean), in units of
                  the 0.45 K healthy residual sd. Thermal faults only.
  latency_h       hours from onset to first correct detection (secondary)
  diag_post%      correct diagnosis over post-detection samples only - the
                  latency window is not double-counted as a diagnosis failure
  diag_all%       the same over the whole fault window, for comparison
  family_post%    right family (asset/sensor) over post-detection samples
  exact_post%     sensor faults: the faulty channel named alone, not as the
                  oil|ambient pair
  wrongfam%       any wrong-family alarm over the whole window, excluding rows
                  where a fault of that family is genuinely present too

Clean samples are split in two. Alarm samples continuous with a fault that has
just ended are alarm-clear latency - an inherent property of a trailing fit
window, reported per event. Every other alarm on a clean sample is a false
alarm.
"""

import json
import sys

import numpy as np
import pandas as pd

from dtwin.physics.nameplate import preset
from dtwin.physics.thermal import ThermalModel
from dtwin.analytics.validation import validate, _runs

pd.set_option("display.width", 220)
head = lambda t: print("\n" + "=" * 90 + f"\n{t}\n" + "=" * 90)

HEALTHY_SD_K = 0.45      # healthy thermal residual sd, docs/methodology.md

IN_SAMPLE = {"showcase": 100, "healthy": 100, "cooling_fault": 100, "summer_overload": 100,
             "sensor_trouble": 100, "unbalance": 100, "oil_loss": 100,
             "winter_250kva_5min": 250}

# What a correct call looks like for each injected fault kind, vectorised.
_sus = lambda rows, ch: rows.suspect_channels.str.split(",").apply(lambda xs: ch in xs).values
EXPECT = {
    "cooling_degradation": ("asset", lambda rows, ch: (rows.thermal_status == "asset_cooling").values),
    "oil_loss":            ("asset", lambda rows, ch: (rows.thermal_status == "asset_cooling").values),
    "overload":            ("asset", lambda rows, ch: rows.overload.values.astype(bool)),
    "unbalance_growth":    ("asset", lambda rows, ch: (rows.elec_status == "asset_unbalance").values),
    "sensor_drift":        ("sensor", _sus),
    "sensor_bias":         ("sensor", _sus),
    "sensor_stuck":        ("sensor", lambda rows, ch: (rows[f"ch_{ch}"] == "stuck").values),
    "sensor_dead":         ("sensor", lambda rows, ch: (rows[f"ch_{ch}"] == "dead").values),
}
STEP_KINDS = {"overload", "sensor_bias", "sensor_stuck", "sensor_dead"}
THERMAL_KINDS = {"cooling_degradation", "oil_loss"}
TRUE_TEMP = {"oil_temp": "true_top_oil_c", "ambient_temp": "true_ambient_c"}


def load(name, data_dir="data"):
    df = pd.read_csv(f"{data_dir}/{name}.csv", parse_dates=["timestamp"], index_col="timestamp")
    truth = pd.read_csv(f"{data_dir}/{name}_truth.csv", parse_dates=["timestamp"],
                        index_col="timestamp")
    return df.drop(columns=["transformer_id"], errors="ignore"), truth


def components(truth):
    """Split the ';'-joined labels into (kind, channel) pairs with row masks."""
    parts = truth.fault_label.str.split(";")
    keys = sorted({p for xs in parts for p in xs if p != "none"})
    return {(p.partition(":")[0], p.partition(":")[2] or None):
            parts.apply(lambda xs, p=p: p in xs).values for p in keys}


def _pct(x):
    return float(np.mean(x)) if len(x) else np.nan


# --------------------------------------------------------------------------- #
# Severity envelope, evaluation only
# --------------------------------------------------------------------------- #
def severity_series(kind, ch, df, truth, clean):
    """How far into its eventual severity the fault is at each sample, 0..1
    once normalised per event. Built from truth (or truth-vs-measured
    differences), so it is evaluation-only."""
    if kind in THERMAL_KINDS:
        return truth.true_cooling_factor.values - 1.0
    if kind == "sensor_drift" and ch in TRUE_TEMP:
        off = (df[ch] - truth[TRUE_TEMP[ch]]).rolling(8, center=True, min_periods=1).median()
        return np.abs(off.values)
    if kind == "sensor_drift":
        return None
    if kind == "unbalance_growth":
        i = df[["IR", "IY", "IB"]]
        u = (i.max(axis=1) - i.min(axis=1)) / i.mean(axis=1)
        u = u.rolling(8, center=True, min_periods=1).median()
        return np.clip(u.values - np.nanmedian(u.values[clean]), 0, None)
    return None


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def score(name, data_dir="data", rating=100):
    df, truth = load(name, data_dir)
    np_ = preset(rating)
    rows, fits, meta = validate(df, np_)
    rows = rows.reindex(truth.index)
    dt_h = (truth.index[1] - truth.index[0]).total_seconds() / 3600.0
    n = len(truth)
    any_alarm = (rows.sensor_alarm | rows.asset_alarm | rows.other_alarm).values.astype(bool)
    clean = (truth.fault_label == "none").values
    resid4h = rows.residual_k.rolling(max(1, int(round(4 / dt_h))), min_periods=1).mean().values

    events = []
    for (kind, ch), mask in components(truth).items():
        family, correct = EXPECT[kind]
        hit = correct(rows, ch)
        fam = rows[f"{family}_alarm"].values.astype(bool)
        wrong = rows["asset_alarm" if family == "sensor" else "sensor_alarm"].values.astype(bool)
        other_present = truth["is_asset_fault" if family == "sensor" else "is_sensor_fault"].values
        sev = severity_series(kind, ch, df, truth, clean)
        exact = None
        if family == "sensor" and ch in TRUE_TEMP:
            sus = rows.suspect_channels.str.split(",")
            others = {"oil_temp", "ambient_temp"} - {ch}
            exact = sus.apply(lambda xs: ch in xs and not (others & set(xs))).values

        for s, e in _runs(mask):
            seg = np.arange(s, e + 1)
            h = hit[seg]
            first = int(np.argmax(h)) if h.any() else None
            post = seg[first:] if first is not None else np.array([], int)
            ev = {"fault": f"{kind}{':' + ch if ch else ''}", "family": family,
                  "start": truth.index[s], "hours": round(len(seg) * dt_h, 1),
                  "detected": h.any(),
                  "latency_h": round(first * dt_h, 2) if first is not None else np.nan,
                  "diag_post": _pct(hit[post]), "diag_all": _pct(h),
                  "family_post": _pct(fam[post]),
                  "exact_post": _pct(exact[post]) if exact is not None else np.nan,
                  "wrongfam": _pct(wrong[seg][~other_present[seg]]),
                  "sev_at_det": np.nan, "resid_sigma": np.nan}
            if kind in STEP_KINDS:
                ev["sev_at_det"] = "step"
            elif sev is not None and first is not None:
                peak = np.nanmax(sev[seg])
                if peak > 0:
                    ev["sev_at_det"] = round(100.0 * sev[seg[first]] / peak, 1)
            if first is not None and (kind in THERMAL_KINDS or ch in TRUE_TEMP):
                ev["resid_sigma"] = round(resid4h[seg[first]] / HEALTHY_SD_K, 1)
            ev["_end"] = e
            events.append(ev)
    ev = pd.DataFrame(events)

    # ---- clean samples: alarm-clear latency vs genuine false alarms ---------
    after_fault = np.zeros(n, bool)          # clean alarm continuous with a fault
    for s, e in _runs(any_alarm):
        faulty = np.where(~clean[s:e + 1])[0]
        if s > 0 and not clean[s - 1]:
            first_fault = 0                  # run began straight after a fault
        elif len(faulty):
            first_fault = faulty[0]
        else:
            continue
        after_fault[s + first_fault:e + 1] = True
    after_fault &= clean
    false_alarm = any_alarm & clean & ~after_fault
    days = n * dt_h / 24.0

    clear = []
    if not ev.empty:
        for i, r in ev.iterrows():
            e = r["_end"]
            j = e + 1
            while j < n and after_fault[j]:
                j += 1
            clear.append(round((j - e - 1) * dt_h, 2))
        ev["clear_h"] = clear
        ev = ev.drop(columns="_end")

    far = {"clean_samples": int(clean.sum()),
           "clear_samples": int(after_fault.sum()),
           "fa_samples": int(false_alarm.sum()),
           "fa_pct": 100.0 * false_alarm.sum() / max((clean & ~after_fault).sum(), 1),
           "fa_episodes": len(_runs(false_alarm)),
           "fa_per_day": len(_runs(false_alarm)) / days,
           "days": days}
    why = rows[false_alarm]
    far["fa_reasons"] = (why.thermal_status.where(why.thermal_status.isin(
        ["asset_cooling", "sensor_temp_offset", "thermal_mismatch"]), "")
        + " " + why.elec_status.where(why.elec_status != "ok", "")
        + " " + why.suspect_channels.fillna("")).str.strip().value_counts().head(6)

    lead = cooling_lead(df, truth, rows, np_, dt_h) if \
        truth.fault_label.str.contains("cooling|oil_loss").any() else None
    return ev, far, lead


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
    first = int(np.argmax(alarm[s:e + 1]) + s) if alarm[s:e + 1].any() else None
    out = {"onset": t0, "first_alarm": truth.index[first] if first is not None else None,
           "excess_at_alarm_k": float(excess[first]) if first is not None else np.nan}
    for lvl in (3.0, 5.0):
        j = np.where((excess >= lvl) & fault)[0]
        out[f"t_plus{int(lvl)}K"] = truth.index[j[0]] if len(j) else None
    over60 = np.where(fault & (df.oil_temp.values > np_.trip_oil_temp_c))[0]
    out["first_60C"] = truth.index[over60[0]] if len(over60) else None
    return out


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _fmt_events(ev):
    show = ev.copy()
    show["start"] = show.start.dt.strftime("d%d %H:%M")
    for c in ("diag_post", "diag_all", "family_post", "exact_post", "wrongfam"):
        show[c] = (100 * show[c]).round(1)
    return show.rename(columns={"sev_at_det": "sev@det%", "resid_sigma": "resid@det_sd",
                                "diag_post": "diag_post%", "diag_all": "diag_all%",
                                "family_post": "family_post%", "exact_post": "exact_post%",
                                "wrongfam": "wrongfam%"})


def report(name, ev, far, lead, label):
    head(f"[{label}] {name.upper()} - per fault event")
    print("  no injected faults" if ev.empty else _fmt_events(ev).to_string(index=False))
    head(f"[{label}] {name.upper()} - clean samples")
    print(f"  clean samples                          {far['clean_samples']}")
    print(f"  alarm-clear latency (after a fault)    {far['clear_samples']} samples "
          f"- see clear_h per event")
    print(f"  FALSE ALARMS, genuinely clean          {far['fa_pct']:.2f}%  "
          f"({far['fa_episodes']} episodes, {far['fa_per_day']:.2f}/day)")
    if len(far["fa_reasons"]):
        print("  what fired:\n" + far["fa_reasons"].to_string())
    if lead:
        head(f"[{label}] {name.upper()} - cooling fault lead time")
        t0 = lead["onset"]
        fmt = lambda t: "never" if t is None else \
            f"{t:%d %b %H:%M}  (onset + {(t - t0).total_seconds() / 3600:.1f} h)"
        print(f"  fault onset                        {t0:%d %b %H:%M}")
        print(f"  first oil > 60 C in fault window   {fmt(lead['first_60C'])}")
        print(f"  first cooling alarm                {fmt(lead['first_alarm'])}"
              f"   fault excess then {lead['excess_at_alarm_k']:.2f} K")
        print(f"  fault adds +3 K                    {fmt(lead['t_plus3K'])}")
        print(f"  fault adds +5 K                    {fmt(lead['t_plus5K'])}")


def run_set(scenarios, data_dir, label, detail=()):
    all_ev, summ = [], []
    tot = {"fa": 0, "base": 0, "ep": 0, "days": 0.0}
    for nm, rating in scenarios.items():
        ev, far, lead = score(nm, data_dir, rating)
        if nm in detail:
            report(nm, ev, far, lead, label)
        if not ev.empty:
            all_ev.append(ev.assign(scenario=nm))
        base = far["clean_samples"] - far["clear_samples"]
        tot["fa"] += far["fa_samples"]; tot["base"] += base
        tot["ep"] += far["fa_episodes"]; tot["days"] += far["days"]
        summ.append({"scenario": nm, "kVA": rating,
                     "events": f"{int(ev.detected.sum()) if not ev.empty else 0}/{len(ev)}",
                     "diag_post%": round(100 * ev.diag_post.mean(), 1) if not ev.empty else np.nan,
                     "wrongfam%": round(100 * ev.wrongfam.mean(), 1) if not ev.empty else np.nan,
                     "FA%": round(far["fa_pct"], 2), "FA ep/day": round(far["fa_per_day"], 2)})

    head(f"[{label}] ALL EVENTS")
    ev = pd.concat(all_ev, ignore_index=True)
    cols = ["scenario"] + [c for c in _fmt_events(ev).columns if c != "scenario"]
    print(_fmt_events(ev)[cols].to_string(index=False))

    head(f"[{label}] PER SCENARIO")
    print(pd.DataFrame(summ).to_string(index=False))

    head(f"[{label}] HEADLINE")
    det = ev[ev.detected]
    ramp = pd.to_numeric(det.sev_at_det, errors="coerce").dropna()
    sd = det.resid_sigma.dropna()
    print(f"  events detected                    {int(ev.detected.sum())} / {len(ev)}")
    print(f"  severity at detection (ramped)     median {ramp.median():.0f}%, "
          f"range {ramp.min():.0f}-{ramp.max():.0f}%  (n={len(ramp)})")
    print(f"  residual at detection (thermal)    median {sd.median():.1f} sd, "
          f"range {sd.min():.1f}-{sd.max():.1f}  (n={len(sd)})")
    print(f"  latency, hours (secondary)         median {det.latency_h.median():.1f} h")
    print(f"  diagnosis, post-detection          {100 * det.diag_post.mean():.1f}%  "
          f"(whole window {100 * ev.diag_all.mean():.1f}%)")
    print(f"  family, post-detection             {100 * det.family_post.mean():.1f}%")
    print(f"  wrong-family alarms                {100 * ev.wrongfam.mean():.2f}% of fault samples")
    print(f"  false alarms, genuinely clean      {100 * tot['fa'] / max(tot['base'], 1):.2f}% "
          f"of samples, {tot['ep']} episodes in {tot['days']:.0f} days "
          f"({tot['ep'] / tot['days']:.2f}/day)")
    print(f"  alarm-clear latency                median {ev.clear_h.median():.1f} h, "
          f"max {ev.clear_h.max():.1f} h")
    return ev


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--set"] and args[1] == "heldout":
        man = json.load(open("data/heldout/manifest.json"))
        run_set({k: v["rating_kva"] for k, v in man.items()}, "data/heldout", "HELD-OUT",
                detail=tuple(man))
    elif args:
        run_set({args[0]: IN_SAMPLE.get(args[0], 100)}, "data", "IN-SAMPLE", detail=(args[0],))
    else:
        run_set(IN_SAMPLE, "data", "IN-SAMPLE", detail=("showcase",))

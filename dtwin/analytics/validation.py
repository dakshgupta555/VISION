"""
Measurement validation layer.

Before the twin is allowed to say anything about the transformer, it has to
decide which of its eight inputs it believes. A monitor that cannot tell a
failing radiator from a failing thermocouple will send a crew to site for the
wrong one, so this layer names the suspect channel rather than just the bad row.

Three tiers, cheapest first:

  PER CHANNEL     dead (missing, zero, out of range), stuck (a run of identical
                  readings longer than chance allows), and step (a jump the
                  quantity cannot physically make between two samples).

  CROSS-PHASE     each phase current and voltage against the other two, relative
                  to the standing pattern at the start of the record. A lone
                  current that moves while the other two agree and the voltages
                  stay put is the CT. A change spread across phases, or one the
                  voltages corroborate, is real load redistribution - an asset
                  condition, not an instrument one.

  THERMAL         the twin's predicted top-oil rise against the measured rise,
                  fitted over a rolling window (docs/methodology.md):

                      measured_rise = slope * predicted_rise + intercept

                  slope away from 1    the transformer changed (cooling path)
                  intercept away from 0 a temperature instrument changed

Faulty current channels are rebuilt from the healthy phases before the thermal
model sees them, so a frozen CT during a cooling fault does not corrupt the
cooling estimate.

Known limit, stated rather than hidden: an oil probe reading high and an
ambient probe reading low shift the measured rise identically. The thermal fit
can say "a temperature instrument is offset" but not which one. A step change
in one channel at onset is used as the tie-breaker; a gradual drift with no step
is reported as the pair.

Batch mode: the standing phase pattern is taken from the first part of the
record, and fits look backward only, so every row uses information available at
that row's timestamp apart from that reference.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import HuberRegressor

from ..physics.nameplate import Nameplate
from ..physics.thermal import ThermalModel
from ..physics import unbalance as ub

CURRENTS = ["IR", "IY", "IB"]
VOLTAGES = ["VR", "VY", "VB"]
TEMPS = ["oil_temp", "ambient_temp"]
CHANNELS = CURRENTS + VOLTAGES + TEMPS


@dataclass
class ValidationConfig:
    # ---- per channel --------------------------------------------------------
    stuck_chance: float = 0.01          # accepted chance of one false stuck run per record
    stuck_min_run: int = 3              # never call fewer identical samples stuck
    step_z: float = 6.0                 # robust z-score of a detrended first difference
    temp_range_c: tuple = (-20.0, 150.0)
    voltage_range_pu: tuple = (0.5, 1.5)
    min_live_current_pu: float = 0.05   # below this a zero current may be genuine

    # ---- cross-phase --------------------------------------------------------
    reference_days: float = 3.0         # standing pattern taken from the record start
    reference_max_frac: float = 0.25
    smooth_samples: int = 4             # trailing median before comparing phases
    current_dev_limit: float = 0.04     # lone-phase current deviation, fractional
    pair_agree_tol: float = 0.02        # the other two must agree this closely
    voltage_corroborate: float = 0.006  # phase voltage deviation that confirms real load
    voltage_dev_limit: float = 0.02     # lone-phase voltage deviation, no current change
    elec_persist_h: float = 1.0         # a cross-phase call must hold this long

    # ---- thermal ------------------------------------------------------------
    fit_window_h: float = 24.0
    fit_step_h: float = 1.0
    fit_min_points: int = 24
    fit_min_spread_k: float = 1.0       # predicted rise must vary to identify a slope
    slope_limit: float = 0.06           # 6% excess rise, ~3 K at a 50 K rise
    intercept_limit_k: float = 2.0
    persist_fits: int = 3               # consecutive fits before an alarm stands
    attribution_h: float = 24.0         # look-back beyond the fit window for a step
    split_test: bool = False            # OFF: failed pre-registered rule 4, see results/prereg_split_step.md
    split_min_side_h: float = 3.0
    split_min_k: float = 1.5
    split_z: float = 6.0

    # ---- loading ------------------------------------------------------------
    overload_pu: float = 1.0


def _robust_sd(x):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return np.nan
    return 1.4826 * np.median(np.abs(x - np.median(x)))


def _runs(mask):
    """(start, end_inclusive) index pairs for each True run."""
    m = np.r_[False, np.asarray(mask, bool), False]
    d = np.diff(m.astype(int))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0] - 1))


# --------------------------------------------------------------------------- #
# Tier 1: per channel
# --------------------------------------------------------------------------- #
def stuck_mask(x, cfg: ValidationConfig):
    """Flag repeats inside a run of identical readings longer than chance.

    The run length is not a fixed number of samples. It is derived from how
    often this channel repeats itself anyway, so a coarsely quantised real
    logger (integer degrees, say) is not flagged for doing what it always does,
    while a 3-decimal channel is caught after a handful of repeats.
    """
    v = np.asarray(x, float)
    n = len(v)
    same = np.zeros(n, bool)
    same[1:] = (v[1:] == v[:-1]) & np.isfinite(v[1:])
    p = float(np.clip(same.mean(), 1e-4, 0.9))
    # a run of N identical readings is N-1 consecutive repeats
    need = int(np.ceil(np.log(cfg.stuck_chance / max(n, 1)) / np.log(p))) + 1
    need = max(need, cfg.stuck_min_run)
    out = np.zeros(n, bool)
    for s, e in _runs(same):
        if e - s + 2 >= need:
            out[s:e + 1] = True          # the first reading of the run is genuine
    return out, need


def step_mask(x, cfg: ValidationConfig):
    """Flag a jump between consecutive samples that the quantity cannot make.

    The first difference is detrended against its own local median, so the
    ordinary diurnal climb is removed and only the step remains.
    """
    v = pd.Series(np.asarray(x, float))
    d = v.diff()
    local = d.rolling(9, center=True, min_periods=3).median()
    r = (d - local).values
    sd = _robust_sd(r)
    if not np.isfinite(sd) or sd <= 0:
        return np.zeros(len(v), bool)
    return np.nan_to_num(np.abs(r) / sd) > cfg.step_z


def dead_mask(df, np_: Nameplate, cfg: ValidationConfig):
    out = {}
    vnom = np_.voltage_nominal_ln_v
    lo_v, hi_v = cfg.voltage_range_pu
    lo_t, hi_t = cfg.temp_range_c
    live = cfg.min_live_current_pu * np_.rated_current_a
    for ch in CURRENTS:
        others = df[[c for c in CURRENTS if c != ch]].median(axis=1)
        v = df[ch]
        out[ch] = (v.isna() | (v < 0) | ((v <= 0) & (others > live))).values
    for ch in VOLTAGES:
        v = df[ch]
        out[ch] = (v.isna() | (v < lo_v * vnom) | (v > hi_v * vnom)).values
    for ch in TEMPS:
        v = df[ch]
        out[ch] = (v.isna() | (v < lo_t) | (v > hi_t)).values
    return out


# --------------------------------------------------------------------------- #
# Tier 2: cross-phase
# --------------------------------------------------------------------------- #
def _reference_slice(index, cfg):
    span = index[-1] - index[0]
    limit = min(pd.Timedelta(days=cfg.reference_days), span * cfg.reference_max_frac)
    return index <= index[0] + limit


def _leave_one_out(block: pd.DataFrame, bad: dict, ref, smooth):
    """Each phase against the mean of the other two, normalised by the standing
    pattern. Returns (deviation, pair_disagreement, standing_ratio)."""
    cols = list(block.columns)
    clean = block.copy()
    for c in cols:
        clean.loc[bad[c], c] = np.nan
    mean = clean.mean(axis=1)
    standing = (clean[ref].div(mean[ref], axis=0)).median()
    norm = clean / standing
    dev, pair = {}, {}
    for k in cols:
        j, l = [c for c in cols if c != k]
        dev[k] = (norm[k] / norm[[j, l]].mean(axis=1) - 1.0)
        pair[k] = (norm[j] / norm[l] - 1.0)
    dev = pd.DataFrame(dev).rolling(smooth, min_periods=1).median()
    pair = pd.DataFrame(pair).rolling(smooth, min_periods=1).median()
    return dev, pair, standing


def cross_phase(df, bad, cfg: ValidationConfig, volts_known=True):
    """Separate a lying CT or VT from genuine unbalance.

    Returns per-row electrical status, the set of suspect channels per row, and
    the standing current pattern used to rebuild a failed phase.
    """
    ref = _reference_slice(df.index, cfg)
    di, pi, s_i = _leave_one_out(df[CURRENTS], bad, ref, cfg.smooth_samples)
    dv, pv, _ = _leave_one_out(df[VOLTAGES], bad, ref, cfg.smooth_samples)

    n = len(df)
    status = np.array(["ok"] * n, dtype=object)
    suspect = {ch: np.zeros(n, bool) for ch in CURRENTS + VOLTAGES}

    adi = di.abs().values
    worst = np.nanargmax(np.where(np.isfinite(adi), adi, -1), axis=1)
    current_moved = np.nanmax(np.where(np.isfinite(adi), adi, 0), axis=1) > cfg.current_dev_limit

    for i in np.where(current_moved)[0]:
        k = worst[i]
        ch_i, ch_v = CURRENTS[k], VOLTAGES[k]
        if bad[ch_i][i]:
            continue                     # already explained by tier 1
        lone = abs(pi.iloc[i, k]) < cfg.pair_agree_tol
        v_dev = abs(dv.iloc[i, k]) if np.isfinite(dv.iloc[i, k]) else 0.0
        if not volts_known:
            v_dev = np.inf               # cannot clear the CT without voltages: call it load
        if lone and v_dev < cfg.voltage_corroborate:
            status[i] = f"sensor_ct:{ch_i}"
            suspect[ch_i][i] = True
        else:
            status[i] = "asset_unbalance"

    adv = dv.abs().values
    for i in (np.where(~current_moved)[0] if volts_known else []):
        row = np.where(np.isfinite(adv[i]), adv[i], 0)
        k = int(np.argmax(row))
        if row[k] > cfg.voltage_dev_limit and abs(pv.iloc[i, k]) < cfg.pair_agree_tol \
                and not bad[VOLTAGES[k]][i]:
            status[i] = f"sensor_vt:{VOLTAGES[k]}"
            suspect[VOLTAGES[k]][i] = True

    # A call must persist before it stands. At the instant a feeder is
    # rebalanced the trailing medians disagree for a sample or two, which would
    # otherwise read as a CT fault.
    # A buffer of one row (a live stream just restarted) has no time step:
    # nothing can persist yet, so nothing is suppressed.
    dt_h = np.median(np.diff(df.index.values).astype("timedelta64[s]").astype(float)) / 3600.0 \
        if len(df) > 1 else np.nan
    need = max(1, int(round(cfg.elec_persist_h / dt_h))) if np.isfinite(dt_h) and dt_h > 0 else 1
    for val in set(status) - {"ok"}:
        for s, e in _runs(status == val):
            if e - s + 1 < need:
                status[s:e + 1] = "ok"
                for m in suspect.values():
                    m[s:e + 1] = False

    return status, suspect, s_i, di


def repair_currents(df, bad, s_i):
    """Rebuild each untrusted phase current from the trusted ones, scaled by the
    standing pattern. Rows with fewer than two trusted phases are left NaN and
    interpolated later, flagged as not thermally valid."""
    cur = df[CURRENTS].copy()
    trusted = pd.DataFrame({c: ~bad[c] for c in CURRENTS}, index=df.index)
    norm = cur.where(trusted) / s_i
    est = norm.mean(axis=1)
    n_ok = trusted.sum(axis=1)
    for c in CURRENTS:
        fill = ~trusted[c] & (n_ok >= 2)
        cur.loc[fill, c] = est[fill] * s_i[c]
        cur.loc[~trusted[c] & (n_ok < 2), c] = np.nan
    repaired = ~trusted.all(axis=1)
    thermal_ok = n_ok >= 2
    return cur, repaired.values, thermal_ok.values


# --------------------------------------------------------------------------- #
# Tier 3: thermal consistency
# --------------------------------------------------------------------------- #
def rolling_rise_fit(index, pred_rise, meas_rise, valid, cfg: ValidationConfig,
                     resets=None):
    """Huber fit over a trailing window, evaluated every fit_step_h:

        measured_rise = slope * predicted_rise + intercept + drift * t

    t is in days, zero at the window end, so the intercept is the offset NOW
    and drift is its rate. Without the time term a probe drifting by 2 K/day is
    aliased into the slope through the diurnal cycle and reads as a cooling
    fault at night; with it, a drifting instrument lands in intercept and drift
    while a degraded cooling path still scales the slope.

    resets marks samples after which earlier data is discarded - a temperature
    step the oil cannot physically make means the instrument changed state.
    Huber rather than least squares so a handful of outliers cannot drag the fit.
    """
    win = pd.Timedelta(hours=cfg.fit_window_h)
    # at coarse cadence a 24 h window holds few samples; ask for 3/4 of them,
    # never more than fit_min_points (unchanged at 45-min cadence or finer)
    step_min = float(np.median(np.diff(index.values).astype("timedelta64[s]").astype(float))) / 60.0 \
        if len(index) > 1 else 15.0
    if not np.isfinite(step_min) or step_min <= 0:
        step_min = 15.0
    min_pts = min(cfg.fit_min_points, max(8, int(0.75 * cfg.fit_window_h * 60.0 / step_min)))
    times = pd.date_range(index[0] + win, index[-1], freq=pd.Timedelta(hours=cfg.fit_step_h))
    reset_pos = np.where(resets)[0] if resets is not None else np.array([], int)
    t_days = (index - index[0]).total_seconds().values / 86400.0
    rows = []
    huber = HuberRegressor(alpha=0.0, max_iter=200)
    split_reset = -1
    for ts in times:
        lo = index.searchsorted(ts - win, side="right")
        hi = index.searchsorted(ts, side="right")
        r = reset_pos[(reset_pos >= lo) & (reset_pos < hi)]
        if len(r):
            lo = int(r[-1])
        lo = max(lo, split_reset)
        m = valid[lo:hi]
        x, y = pred_rise[lo:hi][m], meas_rise[lo:hi][m]
        tt = t_days[lo:hi][m] - t_days[hi - 1]
        split = None
        if cfg.split_test and len(x) >= min_pts:
            split = _split_step(x, y, tt, cfg)
        if split is not None:
            keep = tt > split
            split_reset = int(np.arange(lo, hi)[m][np.argmax(keep)])
            x, y, tt = x[keep], y[keep], tt[keep]
        rec = {"fit_time": ts, "slope": np.nan, "intercept": np.nan,
               "drift_k_per_day": np.nan, "n": len(x), "split_step": split is not None}
        if len(x) >= min_pts and np.std(x) >= cfg.fit_min_spread_k:
            try:
                huber.fit(np.column_stack([x, tt]), y)
                rec["slope"] = float(huber.coef_[0])
                rec["drift_k_per_day"] = float(huber.coef_[1])
                rec["intercept"] = float(huber.intercept_)
            except ValueError:
                pass
        rows.append(rec)
    fits = pd.DataFrame(rows)
    if fits.empty:
        return fits

    raw = np.array(["insufficient"] * len(fits), dtype=object)
    ok = fits.slope.notna().values
    sl, ic = fits.slope.values, fits.intercept.values
    raw[ok] = "ok"
    raw[ok & (sl - 1.0 > cfg.slope_limit)] = "asset_cooling"
    raw[ok & (sl - 1.0 < -cfg.slope_limit)] = "thermal_mismatch"
    raw[ok & (np.abs(sl - 1.0) <= cfg.slope_limit) &
        (np.abs(ic) > cfg.intercept_limit_k)] = "sensor_temp_offset"
    fits["raw_status"] = raw

    # an alarm stands only after persisting for persist_fits consecutive fits
    status = np.array(["ok"] * len(fits), dtype=object)
    status[raw == "insufficient"] = "insufficient"
    run_val, run_len = None, 0
    for i, r in enumerate(raw):
        run_len = run_len + 1 if r == run_val else 1
        run_val = r
        if r not in ("ok", "insufficient") and run_len >= cfg.persist_fits:
            status[i] = r
    fits["status"] = status
    return fits


def _split_step(x, y, tt, cfg):
    """Look for an offset step inside the window that a single fit would
    straddle and alias into the slope. Scans split points, fitting

        y = a*x + b + c*t + S*[t > tau]

    and returns the split time if the best step is both large and certain."""
    side = cfg.split_min_side_h / 24.0
    cands = np.arange(tt.min() + side, tt.max() - side, 1.0 / 24.0)
    best_z, best_tau, best_s = 0.0, None, 0.0
    base = np.column_stack([x, np.ones_like(x), tt])
    for tau in cands:
        X = np.column_stack([base, (tt > tau).astype(float)])
        coef, res, rank, _ = np.linalg.lstsq(X, y, rcond=None)
        if rank < 4:
            continue
        resid = y - X @ coef
        sd = np.sqrt(resid @ resid / max(len(y) - 4, 1))
        se = sd * np.sqrt(np.linalg.inv(X.T @ X)[3, 3])
        z = abs(coef[3]) / se if se > 0 else 0.0
        if z > best_z:
            best_z, best_tau, best_s = z, tau, coef[3]
    if best_tau is not None and best_z >= cfg.split_z and abs(best_s) >= cfg.split_min_k:
        return best_tau
    return None


def attribute_offsets(fits, index, steps, cfg: ValidationConfig):
    """Name the channel behind each temperature-offset episode where a step at
    onset makes that possible; otherwise name the pair."""
    channel = np.array([""] * len(fits), dtype=object)
    look = pd.Timedelta(hours=cfg.fit_window_h + cfg.attribution_h)
    for s, e in _runs(fits.status.values == "sensor_temp_offset"):
        t0 = fits.fit_time.iloc[s]
        sel = (index > t0 - look) & (index <= t0)
        oil_step = steps["oil_temp"][sel].any()
        amb_step = steps["ambient_temp"][sel].any()
        if amb_step and not oil_step:
            name = "ambient_temp"
        elif oil_step and not amb_step:
            name = "oil_temp"
        else:
            name = "oil_temp|ambient_temp"
        channel[s:e + 1] = name
    fits["offset_channel"] = channel
    return fits


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def validate(df: pd.DataFrame, np_: Nameplate, cfg: ValidationConfig = None, absent=()):
    """Run all three tiers. Returns (rows, fits, meta).

    rows   one record per input sample: per-channel status, electrical and
           thermal status, predicted oil, residual, and the two alarm flags
    fits   one record per rolling thermal fit
    meta   thresholds actually used, for display and the report

    absent names channels the source file does not have (see dtwin.ingest).
    They are reported as "absent", never as faults. Missing voltages are taken
    as nominal and the CT-vs-load check cannot clear a CT; missing oil or
    ambient switches the thermal diagnosis off ("unavailable"). With nothing
    absent the behaviour is unchanged.
    """
    cfg = cfg or ValidationConfig()
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="first")]
    absent = set(absent)
    if absent & set(VOLTAGES):
        df = df.copy()
        for c in absent & set(VOLTAGES):
            df[c] = np_.voltage_nominal_ln_v
    thermal_off = bool(absent & {"oil_temp", "ambient_temp"})
    idx = df.index
    n = len(df)

    # ---- tier 1 -------------------------------------------------------------
    dead = dead_mask(df, np_, cfg)
    stuck, need = {}, {}
    steps = {}
    for ch in CHANNELS:
        stuck[ch], need[ch] = stuck_mask(df[ch].values, cfg)
    for ch in TEMPS:
        steps[ch] = step_mask(df[ch].values, cfg)
    for ch in absent:
        dead[ch] = np.zeros(n, bool)
        stuck[ch] = np.zeros(n, bool)
        if ch in steps:
            steps[ch] = np.zeros(n, bool)
    bad = {ch: dead[ch] | stuck[ch] for ch in CHANNELS}

    # ---- tier 2 -------------------------------------------------------------
    elec, suspect, s_i, dev_i = cross_phase(df, bad, cfg,
                                            volts_known=not (absent & set(VOLTAGES)))
    untrusted = {ch: bad[ch] | suspect.get(ch, np.zeros(n, bool)) for ch in CHANNELS}
    cur, cur_repaired, thermal_ok = repair_currents(df, untrusted, s_i)
    cur = cur.interpolate(limit_direction="both")

    volt = df[VOLTAGES].where(~pd.DataFrame({c: untrusted[c] for c in VOLTAGES}, index=idx))
    volt = volt.interpolate(limit_direction="both")
    amb = df.ambient_temp.where(~untrusted["ambient_temp"]).interpolate(limit_direction="both")
    oil = df.oil_temp.where(~untrusted["oil_temp"])

    # ---- tier 3 -------------------------------------------------------------
    an = ub.analyse(cur.IR, cur.IY, cur.IB, volt.VR, volt.VY, volt.VB,
                    np_.rated_current_a, np_.unbalance_limit_pct)
    dt = np.r_[np.nan, np.diff(idx.values).astype("timedelta64[s]").astype(float) / 60.0]
    dt[0] = np.nanmedian(dt[1:]) if n > 1 else 15.0
    pred_oil, pred_hs = ThermalModel(np_).run(an["K_oil"], an["K_winding"], amb.values, dt)
    pred_rise = pred_oil - amb.values
    meas_rise = oil.values - amb.values
    valid = np.isfinite(meas_rise) & thermal_ok & ~untrusted["ambient_temp"]

    temp_step = steps["oil_temp"] | steps["ambient_temp"]
    fits = pd.DataFrame() if thermal_off else \
        rolling_rise_fit(idx, pred_rise, meas_rise, valid, cfg, resets=temp_step)
    if not fits.empty:
        fits = attribute_offsets(fits, idx, steps, cfg)

    # ---- assemble rows ------------------------------------------------------
    rows = pd.DataFrame(index=idx)
    for ch in CHANNELS:
        st = np.array(["ok"] * n, dtype=object)
        if ch in steps:
            st[steps[ch]] = "step"
        st[suspect[ch] if ch in suspect else np.zeros(n, bool)] = "suspect"
        st[stuck[ch]] = "stuck"
        st[dead[ch]] = "dead"
        if ch in absent:
            st[:] = "absent"
        rows[f"ch_{ch}"] = st
    rows["elec_status"] = elec
    # largest phase-current deviation from the standing pattern, the quantity
    # the unbalance call is made on (output only; does not feed any decision)
    rows["phase_dev_max_pct"] = 100.0 * dev_i.abs().max(axis=1).values
    rows["currents_repaired"] = cur_repaired
    rows["K_oil"] = np.asarray(an["K_oil"])
    rows["K_winding"] = np.asarray(an["K_winding"])
    rows["overload"] = rows.K_oil > cfg.overload_pu
    rows["pred_oil_c"] = pred_oil
    rows["pred_hotspot_c"] = pred_hs
    rows["residual_k"] = df.oil_temp.values - pred_oil

    if not fits.empty:
        f = fits[["fit_time", "slope", "intercept", "drift_k_per_day", "status",
                  "offset_channel"]].rename(
            columns={"status": "thermal_status"})
        rows = pd.merge_asof(rows.reset_index().rename(columns={rows.index.name or "index": "ts"}),
                             f, left_on="ts", right_on="fit_time", direction="backward")
        rows = rows.set_index("ts").drop(columns="fit_time")
        rows.index.name = idx.name
        rows["thermal_status"] = rows.thermal_status.fillna("insufficient")
        rows["offset_channel"] = rows.offset_channel.fillna("")
    else:
        rows["slope"] = rows["intercept"] = rows["drift_k_per_day"] = np.nan
        rows["thermal_status"] = "unavailable" if thermal_off else "insufficient"
        rows["offset_channel"] = ""

    # ---- alarms -------------------------------------------------------------
    ch_bad = np.zeros(n, bool)
    for ch in CHANNELS:
        ch_bad |= rows[f"ch_{ch}"].isin(["dead", "stuck", "suspect"]).values
    rows["sensor_alarm"] = (ch_bad
                            | rows.elec_status.str.startswith("sensor").values
                            | (rows.thermal_status == "sensor_temp_offset").values)
    rows["asset_alarm"] = ((rows.thermal_status == "asset_cooling").values
                           | (rows.elec_status == "asset_unbalance").values
                           | rows.overload.values)
    rows["other_alarm"] = (rows.thermal_status == "thermal_mismatch").values
    rows["suspect_channels"] = [_suspects(r) for r in rows.itertuples()]

    meta = {"stuck_run_needed": need, "standing_current_ratio": s_i.round(4).to_dict(),
            "config": cfg.__dict__}
    return rows, fits, meta


def _suspects(r):
    out = [ch for ch in CHANNELS if getattr(r, f"ch_{ch}") in ("dead", "stuck", "suspect")]
    if r.thermal_status == "sensor_temp_offset" and r.offset_channel:
        out += [c for c in r.offset_channel.split("|") if c not in out]
    return ",".join(out)

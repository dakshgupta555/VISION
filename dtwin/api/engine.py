"""
The twin, run over a whole record once, then queried at any instant.

Everything the dashboard shows at time t is computed only from samples at or
before t (the validator's fits look backward), so scrubbing the replay cursor
shows what the twin would have said live at that moment.

Health index
------------
The brief asks for a 0-100 index, so there is one, but it is only a summary of
five physical components, each displayed with its own value and unit.

One definition of "bad" in the whole system: every component is scored on the
SAME quantity, at the same instant, that its alarm or recommendation fires on,
and scores 0 exactly where that alarm fires. 100 is set from healthy behaviour
(p95 of data/healthy.csv) where the quantity has scatter, and from physics
where it does not. Linear in between; ageing on log2 because it doubles every
~7 K.

    component   quantity (instant unless noted)    100 at          0 at (fires)                    weight
    ageing      ageing factor F_AA (log2)           <= 0.5 (103 C)  2.71, hot-spot 120 C (alarm)    0.30
    cooling     rise-fit slope                      <= 1.025        1.06 (cooling alarm)            0.25
    loading     load ratio K                        <= 0.90 pu      1.50 pu; 50 at 1.00 (overload)  0.20
    integrity   channels trusted, last 1 h mean     8 of 8          7 of 8 (sensor alarm)           0.15
    unbalance   max phase deviation from standing   <= 1.45 %       4.0 % (unbalance/CT call)       0.10

Weights are a stated judgement: ageing and cooling consume insulation life;
unbalance is lowest because its heating already reaches the ageing component
through the worst-phase hot-spot.

A channel counts as untrusted when the validator names it suspect for any
reason - dead, stuck, cross-phase disagreement, or a temperature offset found by
the thermal fit (both probes when it cannot tell oil from ambient).

The fixed 60 C trip comparison is NOT an alert and does not touch status or
the index: it is the existing device's behaviour, reported for contrast.
"""

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .. import ingest, labels
from ..analytics.validation import validate, CHANNELS, ValidationConfig, _runs
from ..physics import unbalance as ub
from ..physics.ageing import ageing_factor
from ..physics.nameplate import Nameplate, preset
from ..physics.thermal import (ThermalModel, ThermalState, hotspot_rise_ultimate,
                               top_oil_rise_ultimate)

DATA = Path(__file__).resolve().parents[2] / "data"

_CFG = ValidationConfig()
# Ageing: 100 at half the rated wear rate (F_AA 0.5, hot-spot 103.3 C), 0 at
# the 120 C hot-spot alarm (config/components.yaml winding.hotspot_alarm_c),
# linear in log2 F_AA. Rated load at 30 C (110 C, F_AA 1) scores 59.
HOTSPOT_ALARM_C = 120.0
FAA_ALARM = float(ageing_factor(HOTSPOT_ALARM_C, preset(100)))     # 2.709; F_AA does not depend on rating
# Loading: piecewise linear through these (K pu, score) points. The overload
# alarm fires above 1.0 pu, where the score is 50.
LOADING_POINTS = ((0.9, 100.0), (1.0, 50.0), (1.5, 0.0))
HEALTH = [  # key, weight, value-at-100, value-at-0, unit, label
    ("ageing", 0.30, 0.5, FAA_ALARM, "pu", "Thermal ageing (F_AA)"),
    ("cooling", 0.25, 1.025, 1.0 + _CFG.slope_limit, "", "Cooling (rise-fit slope)"),
    ("loading", 0.20, LOADING_POINTS[0][0], LOADING_POINTS[-1][0], "pu", "Loading (K)"),
    ("integrity", 0.15, 100.0, 100.0 * 7 / 8, "%", "Measurement integrity (1 h)"),
    ("unbalance", 0.10, 1.45, 100.0 * _CFG.current_dev_limit, "%", "Phase deviation"),
]

# The index cannot sit more than CAP_MARGIN above the worst of these: a
# weighted mean alone cannot fall below 50 while the other three components
# stay healthy, even with a 200 C hot-spot.
SAFETY_CRITICAL = ("ageing", "loading")
# How each score runs, in words, for the dashboard and the PDF.
SCALE = {
    "ageing": "100 at F_AA 0.5 or less (hot-spot 103 °C, half the rated wear rate), "
              "0 at F_AA {bad:.2f} (the 120 °C hot-spot alarm); log scale between",
    "cooling": "100 at slope {good:.3f} or less (healthy p95), 0 at {bad:.2f} (the cooling alarm)",
    "loading": "100 at 0.90 pu or less, 50 at 1.00 pu (the overload alarm), 0 at 1.50 pu; linear between",
    "integrity": "100 with every channel trusted, 0 one channel short (the sensor alarm)",
    "unbalance": "100 at {good:.2f} % or less (healthy p95), 0 at {bad:.1f} % (the unbalance / CT call)",
}
CAP_MARGIN = 10.0


def _score(v, good, bad, log2=False):
    if v is None or not np.isfinite(v):
        return None
    if log2:
        v, good, bad = np.log2(max(v, 1e-9)), np.log2(good), np.log2(bad)
    return float(np.clip(100.0 * (bad - v) / (bad - good), 0.0, 100.0)) + 0.0   # no -0


def _f(x, nd=2):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


@dataclass
class Twin:
    name: str
    np_: Nameplate
    df: pd.DataFrame
    rows: pd.DataFrame
    alerts: list = field(default_factory=list)
    reference: list = field(default_factory=list)
    report: object = None
    absent: tuple = ()

    # ------------------------------------------------------------------ build
    @classmethod
    def from_csv(cls, name: str, rating_kva: float = None):
        """Any CSV, through the defensive ingestion layer. The rating comes from
        the request, else the file's sidecar or preamble, else an assumption
        that the report flags."""
        df, rep = ingest.load(DATA / f"{name}.csv", rating_kva=rating_kva)
        extra = {k: rep.nameplate[k] for k in ("hv_kv", "lv_v") if rep.nameplate.get(k)}
        np_ = preset(rep.nameplate["rating_kva"], **extra)
        if rep.nameplate.get("tag"):
            np_.tag = rep.nameplate["tag"]
        rows, fits, meta = validate(df, np_, absent=rep.absent)
        tw = cls(name, np_, df, rows, report=rep, absent=tuple(rep.absent))
        tw._derive()
        tw.alerts = tw._build_alerts()
        return tw

    def _derive(self):
        r, df, np_ = self.rows, self.df, self.np_
        d = df.interpolate(limit_direction="both")
        an = ub.analyse(d.IR, d.IY, d.IB, d.VR, d.VY, d.VB,
                        np_.rated_current_a, np_.unbalance_limit_pct)
        r["current_unbalance_pct"] = np.asarray(an["current_unbalance_pct"])
        r["voltage_unbalance_pct"] = np.asarray(an["voltage_unbalance_iec_vuf_pct"])
        # Hot-spot = MEASURED top oil + modelled winding gradient, when the oil
        # probe is trusted. Under a cooling fault the measured oil already
        # carries the extra rise; a purely predicted hot-spot would miss it.
        gradient = r.pred_hotspot_c - r.pred_oil_c
        oil_ok = r.ch_oil_temp.isin(["ok", "step"]) & ~r.thermal_status.eq("sensor_temp_offset")
        r["hotspot_c"] = np.where(oil_ok, df.oil_temp + gradient, r.pred_hotspot_c)
        r["faa"] = ageing_factor(r.hotspot_c.values, np_)
        dt_h = np.r_[np.nan, np.diff(r.index.values).astype("timedelta64[s]").astype(float) / 3600]
        dt_h[0] = np.nanmedian(dt_h[1:])
        r["dt_h"] = dt_h
        # Above the emergency hot-spot limit protection would have tripped and
        # the unit would be de-energised: those samples add no ageing and no
        # cost, so life used never exceeds what the asset could physically see.
        r["beyond_trip"] = r.hotspot_c > emergency_hotspot_c()
        r["aged_h"] = np.where(r.beyond_trip, 0.0, r.faa * r.dt_h)
        n_sus = r.suspect_channels.fillna("").map(lambda x: len([c for c in x.split(",") if c]))
        self.n_present = len(CHANNELS) - len(self.absent)
        r["trusted_frac"] = 1.0 - n_sus / self.n_present
        r["oil_c"] = df.oil_temp
        r["ambient_c"] = df.ambient_temp
        r["rise_c"] = df.oil_temp - df.ambient_temp

    # ----------------------------------------------------------------- alerts
    def _episodes(self, mask):
        idx = self.rows.index
        return [(idx[s], idx[e]) for s, e in _runs(np.asarray(mask, bool))]

    def _build_alerts(self):
        r, np_ = self.rows, self.np_
        A = []

        def add(t0, t1, level, family, what, why, action, kind, channel=None):
            A.append({"start": t0, "end": t1, "level": level, "family": family,
                      "what": what, "why": why, "action": action, "kind": kind, "channel": channel})

        for t0, t1 in self._episodes(r.thermal_status == "asset_cooling"):
            sl = r.slope.loc[t0]
            add(t0, t1, "alarm", "asset", "Cooling path degraded",
                f"Oil rise is {100 * (sl - 1):.0f}% above what the load and ambient explain "
                f"(fit slope {sl:.2f}); the measured rise scales with load, so the instrument is "
                "not the cause.",
                "Inspect radiators for fouling, check oil level and fans. Schedule within days, "
                "not weeks.", "cooling")
        for t0, t1 in self._episodes(r.thermal_status == "sensor_temp_offset"):
            ic, ch = r.intercept.loc[t0], r.offset_channel.loc[t0]
            names = {"oil_temp": "top-oil probe", "ambient_temp": "ambient probe",
                     "oil_temp|ambient_temp": "top-oil or ambient probe"}
            add(t0, t1, "warn", "sensor", f"Temperature instrument offset ({names.get(ch, ch)})",
                f"Measured rise is shifted by {ic:+.1f} K at every load level; a real thermal fault "
                "would scale with load instead.",
                "Recalibrate or replace the probe. Do not dispatch for a transformer fault; treat "
                "oil-temperature trips as unreliable until fixed.", "temp_offset")
        for t0, t1 in self._episodes(r.thermal_status == "thermal_mismatch"):
            add(t0, t1, "warn", "other", "Thermal model mismatch",
                "Measured rise is lower than the nameplate predicts at this load.",
                "Check the nameplate rating and the oil probe placement.", "mismatch")
        for t0, t1 in self._episodes(r.elec_status == "asset_unbalance"):
            u = r.current_unbalance_pct.loc[t0:t1].max()
            add(t0, t1, "warn", "asset", "Phase load unbalance growing",
                f"Phase currents have moved apart (peak {u:.1f}% unbalance) and the voltages "
                "confirm a real redistribution of load, not a CT error.",
                "Rebalance single-phase loads across R/Y/B at the next feeder visit.", "unbalance")
        lo_t, hi_t = _CFG.temp_range_c
        lo_v, hi_v = (np_.voltage_nominal_ln_v * x for x in _CFG.voltage_range_pu)
        plausible = {**{c: f"{lo_t:.0f} to {hi_t:.0f} °C" for c in ("oil_temp", "ambient_temp")},
                     **{c: f"{lo_v:.0f} to {hi_v:.0f} V" for c in ("VR", "VY", "VB")}}
        for ch in CHANNELS:
            name, raw = labels.READING[ch], self.df[ch]
            dead = r[f"ch_{ch}"].eq("dead")
            for mask, what, why, act in (
                    (dead & raw.isna(), f"{name} reading missing",
                     "The logger sent no value for this channel. Cross-checked against the other channels; "
                     "the twin carries on without it.", "Check wiring and the logger input."),
                    (dead & raw.notna(),
                     f"{name} reading outside the sensor's plausible range" if ch in plausible
                     else f"{name} reads zero or negative while the other phases carry load",
                     (f"A working sensor reports {plausible[ch]}; this reading is outside that, so it is not "
                      "used. A failed probe is the usual cause, but check the load as well." if ch in plausible
                      else "A live phase cannot carry zero or negative current while the others are loaded."),
                     "Check the sensor and its wiring; confirm the value with a hand-held instrument."),
                    (r[f"ch_{ch}"].eq("stuck"), f"{name} reading frozen",
                     "Cross-checked against the other channels; the transformer state is unaffected.",
                     "Reset the logger channel; values during this period were replaced."),
                    (r[f"ch_{ch}"].eq("suspect"), f"{name} disagrees with the other phases",
                     "Cross-checked against the other channels; the transformer state is unaffected.",
                     "Check the CT/VT ratio and connections on this phase.")):
                kind = "channel_stuck" if "frozen" in what else "channel_suspect" if "disagrees" in what \
                    else "channel_dead"
                for t0, t1 in self._episodes(mask):
                    add(t0, t1, "warn", "sensor", what, why, act, kind, channel=ch)
        for t0, t1 in self._episodes(r.overload):
            k = r.K_oil.loc[t0:t1].max()
            add(t0, t1, "alarm", "asset", "Overload",
                f"Load reached {100 * k:.0f}% of rating.",
                "Shed or transfer load; see the time-to-trip countdown.", "overload")
        # what the existing fixed-threshold device would have done
        for t0, t1 in self._episodes(r.oil_c > np_.trip_oil_temp_c):
            amb = r.ambient_c.loc[t0]
            add(t0, t1, "info", "reference", f"Fixed {np_.trip_oil_temp_c:.0f} °C trip would fire",
                f"Oil above {np_.trip_oil_temp_c:.0f} °C at {amb:.0f} °C ambient. The absolute "
                "threshold cannot tell a hot afternoon from a fault.",
                "Compare with the twin's diagnosis above; trip on rise over ambient instead.",
                "fixed_trip")
        A.sort(key=lambda a: a["start"])
        self.reference = [a for a in A if a["kind"] == "fixed_trip"]
        return [a for a in A if a["kind"] != "fixed_trip"]

    # ------------------------------------------------------------------ query
    def locate(self, t):
        idx = self.rows.index
        if t is None:
            return len(idx) - 1
        if isinstance(t, (int, np.integer)):
            return int(np.clip(t, 0, len(idx) - 1))
        return int(np.clip(idx.searchsorted(pd.Timestamp(t), side="right") - 1, 0, len(idx) - 1))

    def health(self, i):
        r = self.rows
        t = r.index[i]
        vals = {
            "ageing": float(r.faa.iloc[i]),
            "cooling": float(r.slope.iloc[i]) if np.isfinite(r.slope.iloc[i]) else 1.0,
            "loading": float(r.K_oil.iloc[i]),
            "integrity": float(100 * r.loc[t - pd.Timedelta(hours=1):t].trusted_frac.mean()),
            "unbalance": float(r.phase_dev_max_pct.iloc[i]),
        }
        comps, tot, wsum = [], 0.0, 0.0
        for key, wt, good, bad, unit, label in HEALTH:
            if key == "integrity":            # 0 = one channel short of all present ones
                bad = 100.0 * (self.n_present - 1) / self.n_present
            if key == "cooling" and r.thermal_status.iloc[i] == "unavailable":
                comps.append({"key": key, "label": label + " - unavailable", "value": None,
                              "unit": unit, "score": None, "weight": wt, "good": good, "bad": bad})
                continue
            if key == "loading":
                x, y = zip(*LOADING_POINTS)
                sc = float(np.interp(vals[key], x, y)) if np.isfinite(vals[key]) else None
            else:
                sc = _score(vals[key], good, bad, log2=(key == "ageing"))
            comps.append({"key": key, "label": label, "value": _f(vals[key], 3), "unit": unit,
                          "score": _f(sc, 0), "weight": wt, "good": good, "bad": bad,
                          "scale": SCALE.get(key, "").format(good=good, bad=bad)})
            if sc is not None:
                tot += wt * sc
                wsum += wt
        mean = tot / wsum if wsum else None
        crit = [c for c in comps if c["key"] in SAFETY_CRITICAL and c["score"] is not None]
        worst = min(crit, key=lambda c: c["score"]) if crit else None
        cap = worst["score"] + CAP_MARGIN if worst else None
        capped = mean is not None and cap is not None and cap < mean
        return {"index": _f(min(mean, cap) if capped else mean, 0) if mean is not None else None,
                "weighted_mean": _f(mean, 0), "capped_by": worst["key"] if capped else None,
                "cap_rule": f"index <= worst of {', '.join(SAFETY_CRITICAL)} + {CAP_MARGIN:g}",
                "components": comps}

    def countdown(self, i):
        return self.whatif(i)

    def whatif(self, i, load_pu=None, ambient_c=None, cooling_factor=None, horizon_h=12.0):
        """Project the twin forward from its state at sample i, holding a load
        and ambient. Defaults are the present values and the twin's own cooling
        estimate, which makes this the live time-to-trip countdown; the what-if
        sliders override them.

        hotspot   the twin's limit: hot-spot at the ageing reference (F_AA = 1).
                  Only this one produces a load-shed instruction.
        fixed     when the existing 60 C device will trip. Reported so the
                  operator can avoid a nuisance outage, never as a diagnosis -
                  an absolute oil threshold is ambient-blind (Finding 1).
        """
        r, np_ = self.rows, self.np_
        ko_now, kw_now = float(r.K_oil.iloc[i]), float(r.K_winding.iloc[i])
        ratio_w = kw_now / ko_now if ko_now > 0 else 1.0
        amb_now = float(r.ambient_c.iloc[i])
        oil = float(r.oil_c.iloc[i]) if r.ch_oil_temp.iloc[i] in ("ok", "step") \
            else float(r.pred_oil_c.iloc[i])
        hs = float(r.hotspot_c.iloc[i])
        cf_est = float(r.slope.iloc[i]) if r.thermal_status.iloc[i] == "asset_cooling" else 1.0
        ko = ko_now if load_pu is None else float(load_pu)
        amb = amb_now if ambient_c is None else float(ambient_c)
        cf = cf_est if cooling_factor is None else float(cooling_factor)
        kw = ko * ratio_w
        model = ThermalModel(np_, cooling_factor=cf)
        # the oil keeps its present rise; a new ambient shifts the absolute level
        state = ThermalState(top_oil_rise_k=oil - amb_now, hotspot_rise_k=hs - oil, ambient_c=amb)
        oil0, hs0 = amb + (oil - amb_now), amb + (hs - amb_now)

        steady_oil = lambda k: steady_top_oil(k, amb, cf, np_)
        steady_hs = lambda k: steady_hotspot_cf(k, ratio_w, amb, cf, np_)

        def project(limit, use, now, fn):
            mins = None if now >= limit else model.minutes_to_threshold(
                state, ko, kw, amb, limit, use=use, horizon_min=60 * horizon_h)
            safe = max_load(fn, limit)
            return {"limit_c": limit, "now_c": _f(now, 1), "minutes": _f(mins, 0),
                    "already_above": now >= limit, "settles_at_c": _f(fn(ko), 1),
                    "safe_load_pu": _f(safe, 3),
                    "shed_pct": _f(max(0.0, 100 * (1 - safe / ko)) if ko > 0 else 0.0, 0)}

        oil_p, hs_p = model.project(state, ko, kw, amb, 60 * horizon_h, dt_minutes=10.0)
        hs_ss = steady_hs(ko)
        faa_ss = float(ageing_factor(hs_ss, np_))
        beyond = bool(hs_ss > emergency_hotspot_c())
        return {"load_pu": _f(ko, 3), "ambient_c": _f(amb, 1), "cooling_factor_used": _f(cf, 3),
                "cooling_factor_estimated": _f(cf_est, 3),
                "hotspot": project(np_.reference_hotspot_c, "hotspot", hs0, steady_hs),
                "fixed": project(np_.trip_oil_temp_c, "top_oil", oil0, steady_oil),
                "steady": {"top_oil_c": _f(steady_oil(ko), 1), "hotspot_c": _f(hs_ss, 1),
                           "faa": _f(faa_ss, 3),
                           "beyond_emergency": beyond,
                           "aged_hours_per_day": None if beyond else _f(24 * faa_ss, 2),
                           "life_cost_inr_per_day": None if beyond else
                           _f(24 * faa_ss * np_.cost_per_hour_of_life_inr, 0)},
                "emergency_hotspot_c": emergency_hotspot_c(),
                "projection": {"minutes": [10 * (k + 1) for k in range(len(oil_p))],
                               "oil_c": [round(float(x), 1) for x in oil_p],
                               "hotspot_c": [round(float(x), 1) for x in hs_p]}}

    def life(self, i):
        """Insulation life consumed, in hours and rupees, at several horizons."""
        r, np_ = self.rows, self.np_
        ts = r.index[i]
        cost = np_.cost_per_hour_of_life_inr

        def win(h):
            w = r.loc[ts - pd.Timedelta(hours=h):ts] if h else r.iloc[:i + 1]
            aged, real = float(w.aged_h.sum()), float(w.dt_h.sum())
            return {"aged_h": _f(aged, 2), "real_h": _f(real, 1), "inr": _f(aged * cost, 0),
                    "rate_pu": _f(aged / real, 3) if real else None,
                    "beyond_trip_h": _f(float(w.dt_h[w.beyond_trip].sum()), 1)}

        return {"cost_per_aged_hour_inr": _f(cost, 2), "replacement_cost_inr": np_.replacement_cost_inr,
                "normal_life_hours": np_.normal_life_hours,
                "emergency_hotspot_c": emergency_hotspot_c(),
                "last_1h": win(1), "last_24h": win(24), "since_start": win(None)}

    def ambient_table(self, i):
        """What each protection philosophy lets the unit carry at each ambient
        (Finding 1). Rise-based trip keeps the 30 K rise their 60 C setting
        implies at the 30 C design ambient."""
        np_ = self.np_
        r = self.rows
        ratio_w = float(r.K_winding.iloc[i] / r.K_oil.iloc[i]) if r.K_oil.iloc[i] > 0 else 1.0
        rise_trip = np_.trip_oil_temp_c - 30.0
        amb_now = float(r.ambient_c.iloc[i])
        rows = []
        for amb in sorted({15, 20, 25, 30, 35, 40, 42, 45, 48, round(amb_now)}):
            rows.append({"ambient_c": amb,
                         "fixed_trip_pu": _f(max_load(lambda k: steady_top_oil(k, amb, 1.0, np_),
                                                      np_.trip_oil_temp_c), 2),
                         "rise_trip_pu": _f(max_load(lambda k: steady_top_oil(k, 0.0, 1.0, np_),
                                                     rise_trip), 2),
                         "hotspot_pu": _f(max_load(lambda k: steady_hotspot_cf(k, ratio_w, amb, 1.0, np_),
                                                   np_.reference_hotspot_c), 2),
                         "current": amb == round(amb_now)})
        return {"rise_trip_k": rise_trip, "fixed_trip_c": np_.trip_oil_temp_c,
                "hotspot_limit_c": np_.reference_hotspot_c, "rows": rows}

    def gprs_payload(self, i, live, cd):
        """Compact alert message for the existing GPRS link.

        FORMAT ASSUMED: CPRI's device message format is not in hand, so this is
        a comma-separated SMS-length frame; replace the field order once their
        format is known. Kept under 160 characters."""
        r, np_ = self.rows, self.np_
        row = r.iloc[i]
        code = {"cooling": "COOL", "temp_offset": "TOFS", "mismatch": "TMIS", "unbalance": "UNB",
                "overload": "OVL", "channel_dead": "CHDEAD", "channel_stuck": "CHSTK",
                "channel_suspect": "CHSUS"}
        lvl = "ALM" if any(a["level"] == "alarm" for a in live) else \
            "WRN" if live else "OK"
        codes = "+".join(dict.fromkeys(code.get(a["kind"], "OTH") for a in live)) or "NONE"
        fam = "".join(sorted({a["family"][0].upper() for a in live})) or "-"
        h = cd["hotspot"]
        ttt = "NOW" if h["already_above"] else (f"{h['minutes']:.0f}" if h["minutes"] is not None else "-")
        fields = [np_.tag, r.index[i].strftime("%y%m%d%H%M"), lvl, codes, fam,
                  f"OT{row.oil_c:.0f}", f"HS{row.hotspot_c:.0f}", f"LD{100 * row.K_oil:.0f}",
                  f"TT{ttt}", f"SH{h['shed_pct']:.0f}", (row.suspect_channels or "-").replace(",", "/")]
        msg = ",".join(fields)
        return {"message": msg[:160], "length": len(msg), "format": "assumed",
                "fields": ["tag", "yymmddHHMM", "level", "codes", "family A/S", "oil C", "hotspot C",
                           "load %", "min to hotspot limit", "shed %", "suspect channels"]}

    def state(self, t=None):
        i = self.locate(t)
        r, np_ = self.rows, self.np_
        row = r.iloc[i]
        ts = r.index[i]
        live = [a for a in self.alerts if a["start"] <= ts <= a["end"]]
        recent = [a for a in self.alerts if a["start"] <= ts][-12:][::-1]
        ref = [a for a in self.reference if a["start"] <= ts <= a["end"]]
        trips_24h = sum(1 for a in self.reference
                        if a["start"] <= ts and a["end"] >= ts - pd.Timedelta(hours=24))
        day = r.loc[ts - pd.Timedelta(hours=24):ts]
        level = "alarm" if any(a["level"] == "alarm" for a in live) else \
            "warn" if any(a["level"] == "warn" for a in live) else "ok"
        cd = self.countdown(i)
        return {
            "scenario": self.name, "i": i, "n": len(r), "timestamp": ts.isoformat(),
            "nameplate": {"tag": np_.tag, "rating_kva": np_.rating_kva, "hv_kv": np_.hv_kv,
                          "lv_v": np_.lv_v, "cooling": np_.cooling,
                          "rated_current_a": round(np_.rated_current_a, 1)},
            "status": {"level": level, "asset_alarm": bool(row.asset_alarm),
                       "sensor_alarm": bool(row.sensor_alarm),
                       "thermal_status": row.thermal_status, "elec_status": row.elec_status,
                       "suspect_channels": row.suspect_channels},
            "readings": {
                "oil_c": _f(row.oil_c, 1), "oil_pred_c": _f(row.pred_oil_c, 1),
                "hotspot_c": _f(row.hotspot_c, 1), "ambient_c": _f(row.ambient_c, 1),
                "rise_k": _f(row.rise_c, 1), "residual_k": _f(row.residual_k, 2),
                "load_pct": _f(100 * row.K_oil, 0), "load_worst_phase_pct": _f(100 * row.K_winding, 0),
                "current_unbalance_pct": _f(row.current_unbalance_pct, 1),
                "voltage_unbalance_pct": _f(row.voltage_unbalance_pct, 2),
                "faa": _f(row.faa, 3), "slope": _f(row.slope, 3), "intercept_k": _f(row.intercept, 2),
                # fit evaluated now: t = 0 at the window end, so the drift term adds c*0
                "excess_rise_k": _f(row.intercept + row.drift_k_per_day * 0.0, 2),
                "currents_a": {c: _f(self.df[c].iloc[i], 1) for c in ("IR", "IY", "IB")},
                "voltages_v": {c: _f(self.df[c].iloc[i], 1) for c in ("VR", "VY", "VB")},
                "channels": {c: row[f"ch_{c}"] for c in CHANNELS},
            },
            "ageing_24h": {
                "equivalent_hours": _f(day.aged_h.sum(), 2),
                "real_hours": _f(day.dt_h.sum(), 1),
                "life_cost_inr": _f(day.aged_h.sum() * np_.cost_per_hour_of_life_inr, 0),
                "beyond_trip_h": _f(float(day.dt_h[day.beyond_trip].sum()), 1),
                "last_hour_equivalent_hours": _f(r.loc[ts - pd.Timedelta(hours=1):ts].aged_h.sum(), 2),
            },
            "health": self.health(i),
            "physical_limit": {"emergency_hotspot_c": emergency_hotspot_c(),
                               "beyond": bool(row.beyond_trip),
                               "message": BEYOND_LIMIT if row.beyond_trip else None},
            "countdown": cd,
            "alerts_live": [{**_alert_json(a), "headline": self._headline(a, row)}
                            for a in sorted(live, key=_urgency)],
            "alerts_recent": [_alert_json(a) for a in recent],
            "alert_groups": self._groups(ts),
            "reference": {"fixed_trip_active": bool(ref),
                          "since": ref[0]["start"].isoformat() if ref else None,
                          "trip_episodes_24h": trips_24h,
                          "note": ref[0]["why"] if ref else None},
            "recommendations": self._recommend(live, cd),
            "data_source": self.data_source(),
            "life": self.life(i),
            "ambient_table": self.ambient_table(i),
            "gprs": self.gprs_payload(i, live, cd),
            "display_limits": self.display_limits(),
        }

    # Health-index colour bands of the dashboard (green above 80, red below 40).
    HEALTH_BANDS = {"warn_below": 80, "alarm_below": 40,
                    "source": "dashboard colour rule: green > 80, amber 40-80, red < 40"}

    def display_limits(self):
        """Warning and alarm levels the dashboard draws against. Presentation
        only: the detector and the alarms do not read these."""
        from ..twin.components import config as comp_config
        cfg = comp_config()
        w = cfg["winding"]
        oil = next(c for c in cfg["components"] if c["id"] == "oil")["thresholds"]
        np_ = self.np_
        v, tol = np_.voltage_nominal_ln_v, np_.voltage_tolerance_pct
        return {
            "hotspot_c": {"warn": w["hotspot_warn_c"], "alarm": w["hotspot_alarm_c"], "limit": np_.reference_hotspot_c,
                          "min": 20, "max": w["hotspot_alarm_c"] + 30,
                          "source": "config/components.yaml winding; limit = nameplate ageing reference"},
            "load_pct": {"warn": 100 * w["load_warn_pu"], "alarm": 100 * w["load_alarm_pu"], "min": 0, "max": 150,
                         "source": "config/components.yaml winding"},
            "top_oil_c": {"warn": oil["top_oil_warn_c"], "alarm": oil["top_oil_alarm_c"], "min": 0,
                          "max": oil["top_oil_alarm_c"] + 15, "source": oil["source"]},
            "health": {**self.HEALTH_BANDS, "min": 0, "max": 100},
            "voltage_v": {"nominal": v, "low": round(v * (1 - tol / 100), 1), "high": round(v * (1 + tol / 100), 1),
                          "tolerance_pct": tol, "source": "nameplate nominal and tolerance"},
            "existing_trip_oil_c": np_.trip_oil_temp_c,
        }

    def data_source(self):
        rep = self.report
        return {"rating_kva": rep.nameplate.get("rating_kva"), "rating_how": rep.nameplate.get("how"),
                "absent": rep.absent, "derived": rep.derived, "warnings": rep.warnings,
                "cadence_min": rep.cadence.get("median_min"),
                "resampled_to_min": rep.cadence.get("resampled_to_min"),
                "rows": rep.rows, "thermal_available": not ({"oil_temp", "ambient_temp"} & set(rep.absent))}

    def _groups(self, ts, limit=12):
        """Repeated episodes of the same fault collapsed into one entry, as
        known at ts: an episode still running at ts has no end yet."""
        groups = {}
        for a in self.alerts:
            if a["start"] > ts:
                break
            g = groups.setdefault((a["kind"], a["what"]), {
                "kind": a["kind"], "what": a["what"], "family": a["family"], "episodes": []})
            active = a["end"] >= ts
            g["episodes"].append({"start": a["start"].isoformat(),
                                  "end": None if active else a["end"].isoformat(), "active": active})
            g.update(level=a["level"], why=a["why"], action=a["action"])   # latest episode
        out = []
        for g in groups.values():
            ep = g["episodes"]
            g.update(count=len(ep), first=ep[0]["start"], last=ep[-1]["end"] or ep[-1]["start"],
                     active=any(e["active"] for e in ep))
            g["episodes"] = ep[::-1]
            out.append(g)
        act = [g for g in out if g["active"]]
        old = sorted([g for g in out if not g["active"]], key=lambda g: g["last"], reverse=True)
        return (act + old)[:limit]

    SHORT = {"cooling": "inspect radiators, oil level and fans",
             "temp_offset": "recalibrate the probe; do not dispatch",
             "mismatch": "check the nameplate and probe placement",
             "unbalance": "rebalance at next feeder visit",
             "overload": "shed or transfer load",
             "channel_dead": "check wiring and logger input",
             "channel_stuck": "reset the logger channel",
             "channel_suspect": "check CT/VT ratio and connections"}

    def _headline(self, a, row):
        """Issue · its value NOW · what to do, for the main headline."""
        k = a["kind"]
        val = {"cooling": f"rise +{100 * (row.slope - 1):.0f}% over design",
               "temp_offset": f"{row.intercept:+.1f} K offset",
               "mismatch": f"slope {row.slope:.2f}",
               "unbalance": f"{row.current_unbalance_pct:.1f}%",
               "overload": f"{100 * row.K_oil:.0f}% load"}.get(k)
        parts = [a["what"]] + ([val] if val else []) + [self.SHORT.get(k, a["action"])]
        return " · ".join(parts)

    def _recommend(self, live, cd):
        """One action per problem. The hot-spot projection carries the load
        instruction, so an overload alert does not add a second "shed load"
        line: the two merge into the projection's one."""
        out = []
        h = cd["hotspot"]
        shed = h["already_above"] or h["minutes"] is not None
        for a in live:
            if a["kind"] == "overload" and shed:
                continue
            out.append({"priority": 1 if a["level"] == "alarm" else 2, "family": a["family"],
                        "text": a["action"], "because": a["what"]})
        if shed:
            lim = f"{h['limit_c']:.0f}\u00a0°C"            # no line break inside "110 °C"
            text = (f"Shed {h['shed_pct']:.0f}% of load to bring the hot-spot back under {lim}."
                    if h["already_above"] else
                    f"Shed {h['shed_pct']:.0f}% of load to keep the hot-spot under {lim}; "
                    f"at this load it gets there in {h['minutes']:.0f} min.")
            out.append({"priority": 1, "family": "asset", "text": text,
                        "detail": f"If nothing changes it settles at {h['settles_at_c']:.0f}\u00a0°C.",
                        "because": "Insulation would age faster than its rated life"})
        f = cd["fixed"]
        if f["minutes"] is not None:
            out.append({"priority": 2, "family": "reference",
                        "text": f"Existing device trips in {f['minutes']:.0f} min "
                                f"({f['limit_c']:.0f} °C oil). Shed {f['shed_pct']:.0f}% only if an "
                                "outage must be avoided; the insulation is not at risk.",
                        "because": "Fixed threshold, ambient-blind"})
        if not out:
            out.append({"priority": 3, "family": "none", "text": "No action needed.",
                        "because": "All channels trusted, thermal behaviour as designed"})
        return sorted(out, key=lambda x: x["priority"])

    def series(self, t=None, hours=48.0, max_points=400):
        i = self.locate(t)
        r = self.rows
        ts = r.index[i]
        w = r.loc[ts - pd.Timedelta(hours=hours):ts]
        step = max(1, len(w) // max_points)
        w = w.iloc[::step]
        cols = {"oil_c": 1, "pred_oil_c": 1, "hotspot_c": 1, "ambient_c": 1, "residual_k": 2,
                "K_oil": 3, "faa": 3, "current_unbalance_pct": 2, "slope": 3, "intercept": 2}
        out = {"t": [x.isoformat() for x in w.index]}
        for c, nd in cols.items():
            out[c] = [None if not np.isfinite(v) else round(float(v), nd) for v in w[c].values]
        for c in ("IR", "IY", "IB", "VR", "VY", "VB"):
            v = self.df[c].reindex(w.index).values
            out[c] = [None if not np.isfinite(x) else round(float(x), 1) for x in v]
        out["rated_current_a"] = round(self.np_.rated_current_a, 1)
        out["trip_c"] = self.np_.trip_oil_temp_c
        return out

    def timeline(self, max_points=600):
        """Coarse whole-record strip for the replay scrubber."""
        r = self.rows
        step = max(1, len(r) // max_points)
        w = r.iloc[::step]
        lvl = np.where(w.asset_alarm, 2, np.where(w.sensor_alarm, 1, 0))
        idx = r.index
        episodes = [{"what": a["what"], "level": a["level"], "family": a["family"], "kind": a["kind"],
                     "start": a["start"].isoformat(), "end": a["end"].isoformat(),
                     "i0": int(idx.get_indexer([a["start"]], method="nearest")[0]),
                     "i1": int(idx.get_indexer([a["end"]], method="nearest")[0])}
                    for a in self.alerts]
        return {"t": [x.isoformat() for x in w.index], "i": list(range(0, len(r), step))[:len(w)],
                "level": lvl.tolist(), "oil_c": [round(float(v), 1) for v in w.oil_c.values],
                "n": len(r), "episodes": episodes}


def emergency_hotspot_c():
    """IEC 60076-7 long-time emergency hot-spot limit for distribution
    transformers (config/components.yaml winding.hotspot_emergency_c)."""
    from ..twin.components import config as comp_config
    return float(comp_config()["winding"]["hotspot_emergency_c"])


BEYOND_LIMIT = "Beyond IEC emergency limit — protection would trip; model extrapolating"


def steady_top_oil(k, ambient_c, cf, np_):
    return ambient_c + cf * top_oil_rise_ultimate(k, np_)


def steady_hotspot_cf(k, ratio_w, ambient_c, cf, np_):
    return steady_top_oil(k, ambient_c, cf, np_) + hotspot_rise_ultimate(k * ratio_w, np_)


def max_load(fn, limit):
    """Largest load ratio whose steady-state temperature fn(k) stays at or
    under limit; 0 if even no load exceeds it."""
    if fn(0.0) > limit:
        return 0.0
    lo, hi = 0.0, 3.0
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if fn(mid) <= limit else (lo, mid)
    return lo


@lru_cache(maxsize=8)
def selftest(rating_kva: float = 100.0, seed: int = 9001):
    """Fault-injection self-test: inject cooling degradation of increasing
    severity into a fresh simulated record for this nameplate and measure the
    smallest one the twin detects, how fast, and whether anything fires before
    the fault. A measured detection limit, not an asserted one."""
    from ..simulate.generator import TransformerSimulator, SimConfig
    from ..simulate import faults as F
    np_ = preset(rating_kva)
    out = []
    for sev in (0.02, 0.04, 0.06, 0.08, 0.10, 0.15, 0.20, 0.30):
        start = 6 * 24
        f = F.cooling_degradation(start_h=start, end_h=start + 6 * 24, severity=sev, ramp_h=12)
        df, truth = TransformerSimulator(np_, SimConfig(days=13, seed=seed)).generate(custom_faults=[f])
        rows, _, _ = validate(df.drop(columns=["transformer_id"]), np_)
        fault = (truth.fault_label != "none").values
        hit = rows.thermal_status.eq("asset_cooling").values
        first = int(np.argmax(hit & fault)) if (hit & fault).any() else None
        t0 = int(np.argmax(fault))
        dt_h = (rows.index[1] - rows.index[0]).total_seconds() / 3600
        out.append({"severity_pct": round(100 * sev), "detected": first is not None,
                    "latency_h": round((first - t0) * dt_h, 1) if first is not None else None,
                    "extra_rise_at_rated_k": round(sev * np_.top_oil_rise_rated_k, 1),
                    "alarm_before_fault": bool(hit[:t0].any())})
    det = [x["severity_pct"] for x in out if x["detected"]]
    smallest = None
    for x in out:                       # smallest severity from which every larger one is caught
        if all(y["detected"] for y in out if y["severity_pct"] >= x["severity_pct"]):
            smallest = x["severity_pct"]
            break
    return {"rating_kva": rating_kva, "seed": seed, "runs": out,
            "min_detectable_pct": smallest,
            "note": "Cooling degradation, 12 h onset, held 6 days, fresh simulated record. "
                    "Severity = fractional increase in top-oil rise."}


def _urgency(a):
    """Alarms before warnings; within a level, asset before sensor."""
    return ({"alarm": 0, "warn": 1}.get(a["level"], 2), {"asset": 0, "sensor": 1}.get(a["family"], 2))


def _alert_json(a):
    return {**a, "start": a["start"].isoformat(), "end": a["end"].isoformat()}


@lru_cache(maxsize=32)
def get_twin(name: str, rating_kva: float = None) -> Twin:
    return Twin.from_csv(name, rating_kva)


def scenarios():
    return sorted(p.stem for p in DATA.glob("*.csv") if not p.stem.endswith("_truth"))

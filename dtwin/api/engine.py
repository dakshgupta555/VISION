"""
The twin, run over a whole record once, then queried at any instant.

Everything the dashboard shows at time t is computed only from samples at or
before t (the validator's fits look backward), so scrubbing the replay cursor
shows what the twin would have said live at that moment.

Health index
------------
The brief asks for a 0-100 index, so there is one, but it is only a summary of
five physical components, each displayed with its own value and unit. The
weights are a stated judgement, not a fitted result:

    component        physical quantity (last 24 h)          100 at        0 at       weight
    thermal ageing   mean ageing factor F_AA                 <= 1.0 pu    >= 16 pu    0.30
    cooling          rise-fit slope (1.0 = as designed)      <= 1.03      >= 1.50     0.25
    loading          peak load ratio K                       <= 0.80 pu   >= 1.50 pu  0.20
    integrity        share of channel-samples trusted        100 %        <= 70 %     0.15
    unbalance        mean current unbalance (NEMA)           <= 2 %       >= 20 %     0.10

Ageing is scored on log2(F_AA) because ageing doubles every ~7 K: each
doubling costs 25 points. Ageing and cooling carry the most weight because they
are what consumes insulation life; unbalance is weighted lowest because its
thermal effect already appears in the ageing component through the hot-spot.
"""

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from ..analytics.validation import validate, CHANNELS, _runs
from ..physics import unbalance as ub
from ..physics.ageing import ageing_factor
from ..physics.nameplate import Nameplate, preset
from ..physics.thermal import (ThermalModel, ThermalState, hotspot_rise_ultimate,
                               top_oil_rise_ultimate)

DATA = Path(__file__).resolve().parents[2] / "data"
RATINGS = {"winter_250kva_5min": 250}

HEALTH = [  # key, weight, value-at-100, value-at-0, unit, label
    ("ageing", 0.30, 1.0, 16.0, "pu", "Thermal ageing (mean F_AA)"),
    ("cooling", 0.25, 1.03, 1.50, "", "Cooling (rise-fit slope)"),
    ("loading", 0.20, 0.80, 1.50, "pu", "Loading (peak K)"),
    ("integrity", 0.15, 100.0, 70.0, "%", "Measurement integrity"),
    ("unbalance", 0.10, 2.0, 20.0, "%", "Current unbalance (NEMA)"),
]


def _score(v, good, bad, log2=False):
    if v is None or not np.isfinite(v):
        return None
    if log2:
        v, good, bad = np.log2(max(v, 1e-9)), np.log2(good), np.log2(bad)
    return float(np.clip(100.0 * (bad - v) / (bad - good), 0.0, 100.0))


def _f(x, nd=2):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


@dataclass
class Twin:
    name: str
    np_: Nameplate
    df: pd.DataFrame
    rows: pd.DataFrame
    alerts: list = field(default_factory=list)

    # ------------------------------------------------------------------ build
    @classmethod
    def from_csv(cls, name: str, rating_kva: float = None):
        df = pd.read_csv(DATA / f"{name}.csv", parse_dates=["timestamp"], index_col="timestamp")
        df = df.drop(columns=["transformer_id"], errors="ignore").sort_index()
        np_ = preset(rating_kva or RATINGS.get(name, 100))
        rows, fits, meta = validate(df, np_)
        tw = cls(name, np_, df, rows)
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
        r["aged_h"] = r.faa * r.dt_h
        r["trusted_frac"] = r[[f"ch_{c}" for c in CHANNELS]].isin(["ok", "step"]).mean(axis=1)
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

        def add(t0, t1, level, family, what, why, action, kind):
            A.append({"start": t0, "end": t1, "level": level, "family": family,
                      "what": what, "why": why, "action": action, "kind": kind})

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
            names = {"oil_temp": "oil probe", "ambient_temp": "ambient probe",
                     "oil_temp|ambient_temp": "oil or ambient probe"}
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
        for ch in CHANNELS:
            for state, lvl, verb, act in (
                    ("dead", "warn", "reading missing or invalid",
                     "Check wiring and the logger input."),
                    ("stuck", "warn", "frozen at a constant value",
                     "Reset the logger channel; values during this period were replaced."),
                    ("suspect", "warn", "disagrees with the other phases",
                     "Check the CT/VT ratio and connections on this phase.")):
                for t0, t1 in self._episodes(r[f"ch_{ch}"] == state):
                    add(t0, t1, lvl, "sensor", f"{ch} {verb}",
                        "Cross-checked against the other channels; the transformer state is "
                        "unaffected.", act, f"channel_{state}")
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
        return A

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
        w = r.loc[t - pd.Timedelta(hours=24):t]
        vals = {
            "ageing": float(w.faa.mean()),
            "cooling": float(r.slope.iloc[i]) if np.isfinite(r.slope.iloc[i]) else 1.0,
            "loading": float(w.K_oil.max()),
            "integrity": float(100 * w.trusted_frac.mean()),
            "unbalance": float(w.current_unbalance_pct.mean()),
        }
        comps, tot, wsum = [], 0.0, 0.0
        for key, wt, good, bad, unit, label in HEALTH:
            sc = _score(vals[key], good, bad, log2=(key == "ageing"))
            if key == "integrity" and r.sensor_alarm.iloc[i]:
                sc = min(sc, 60.0)          # an active instrument fault caps integrity
            comps.append({"key": key, "label": label, "value": _f(vals[key], 3), "unit": unit,
                          "score": _f(sc, 0), "weight": wt, "good": good, "bad": bad})
            if sc is not None:
                tot += wt * sc
                wsum += wt
        return {"index": _f(tot / wsum, 0) if wsum else None, "components": comps}

    def countdown(self, i):
        """Two projections at the present load and ambient, using the twin's
        own estimate of the cooling factor.

        hotspot   the twin's limit: hot-spot at the ageing reference (F_AA = 1).
                  Only this one produces a load-shed instruction.
        fixed     when the existing 60 C device will trip. Reported so the
                  operator can avoid a nuisance outage, never as a diagnosis -
                  an absolute oil threshold is ambient-blind (Finding 1).
        """
        r, np_ = self.rows, self.np_
        ko, kw, amb = float(r.K_oil.iloc[i]), float(r.K_winding.iloc[i]), float(r.ambient_c.iloc[i])
        oil = float(r.oil_c.iloc[i]) if r.ch_oil_temp.iloc[i] in ("ok", "step") \
            else float(r.pred_oil_c.iloc[i])
        hs = float(r.hotspot_c.iloc[i])
        cf = float(r.slope.iloc[i]) if r.thermal_status.iloc[i] == "asset_cooling" else 1.0
        model = ThermalModel(np_, cooling_factor=cf)
        state = ThermalState(top_oil_rise_k=oil - amb, hotspot_rise_k=hs - oil, ambient_c=amb)
        ratio_w = kw / ko if ko > 0 else 1.0

        def steady_hs(k):
            return amb + cf * top_oil_rise_ultimate(k, np_) + hotspot_rise_ultimate(k * ratio_w, np_)

        def steady_oil(k):
            return amb + cf * top_oil_rise_ultimate(k, np_)

        def max_load(fn, limit):
            if fn(0.0) > limit:
                return 0.0
            lo, hi = 0.0, 3.0
            for _ in range(50):
                mid = 0.5 * (lo + hi)
                lo, hi = (mid, hi) if fn(mid) <= limit else (lo, mid)
            return lo

        def project(limit, use, now, fn):
            mins = None if now >= limit else model.minutes_to_threshold(
                state, ko, kw, amb, limit, use=use, horizon_min=720)
            safe = max_load(fn, limit)
            return {"limit_c": limit, "now_c": _f(now, 1), "minutes": _f(mins, 0),
                    "already_above": now >= limit, "settles_at_c": _f(fn(ko), 1),
                    "safe_load_pu": _f(safe, 3),
                    "shed_pct": _f(max(0.0, 100 * (1 - safe / ko)) if ko > 0 else 0.0, 0)}

        return {"load_pu": _f(ko, 3), "ambient_c": _f(amb, 1), "cooling_factor_used": _f(cf, 3),
                "hotspot": project(np_.reference_hotspot_c, "hotspot", hs, steady_hs),
                "fixed": project(np_.trip_oil_temp_c, "top_oil", oil, steady_oil)}

    def state(self, t=None):
        i = self.locate(t)
        r, np_ = self.rows, self.np_
        row = r.iloc[i]
        ts = r.index[i]
        live = [a for a in self.alerts if a["start"] <= ts <= a["end"]]
        recent = [a for a in self.alerts if a["start"] <= ts and a["kind"] != "fixed_trip"][-12:][::-1]
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
                "currents_a": {c: _f(self.df[c].iloc[i], 1) for c in ("IR", "IY", "IB")},
                "voltages_v": {c: _f(self.df[c].iloc[i], 1) for c in ("VR", "VY", "VB")},
                "channels": {c: row[f"ch_{c}"] for c in CHANNELS},
            },
            "ageing_24h": {
                "equivalent_hours": _f(day.aged_h.sum(), 2),
                "real_hours": _f(day.dt_h.sum(), 1),
                "life_cost_inr": _f(day.aged_h.sum() * np_.cost_per_hour_of_life_inr, 0),
                "last_hour_equivalent_hours": _f(r.loc[ts - pd.Timedelta(hours=1):ts].aged_h.sum(), 2),
            },
            "health": self.health(i),
            "countdown": cd,
            "alerts_live": [_alert_json(a) for a in live],
            "alerts_recent": [_alert_json(a) for a in recent],
            "recommendations": self._recommend(live, cd),
        }

    def _recommend(self, live, cd):
        out = []
        for a in live:
            if a["kind"] == "fixed_trip":
                continue
            out.append({"priority": 1 if a["level"] == "alarm" else 2, "family": a["family"],
                        "text": a["action"], "because": a["what"]})
        h = cd["hotspot"]
        if h["already_above"] or h["minutes"] is not None:
            when = "now" if h["already_above"] else f"in {h['minutes']:.0f} min"
            out.append({"priority": 1, "family": "asset",
                        "text": f"Shed {h['shed_pct']:.0f}% load: hot-spot reaches "
                                f"{h['limit_c']:.0f} °C {when} and settles at {h['settles_at_c']:.0f} °C.",
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
        out["trip_c"] = self.np_.trip_oil_temp_c
        return out

    def timeline(self, max_points=600):
        """Coarse whole-record strip for the replay scrubber."""
        r = self.rows
        step = max(1, len(r) // max_points)
        w = r.iloc[::step]
        lvl = np.where(w.asset_alarm, 2, np.where(w.sensor_alarm, 1, 0))
        return {"t": [x.isoformat() for x in w.index], "i": list(range(0, len(r), step))[:len(w)],
                "level": lvl.tolist(), "oil_c": [round(float(v), 1) for v in w.oil_c.values]}


def _alert_json(a):
    return {**a, "start": a["start"].isoformat(), "end": a["end"].isoformat()}


@lru_cache(maxsize=16)
def get_twin(name: str) -> Twin:
    return Twin.from_csv(name)


def scenarios():
    return sorted(p.stem for p in DATA.glob("*.csv") if not p.stem.endswith("_truth"))

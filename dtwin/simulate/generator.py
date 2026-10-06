"""
Synthetic SCADA-style data for a distribution transformer.

Produces the schema the CPRI monitoring device actually records - IR, IY, IB,
VR, VY, VB, oil temperature, ambient temperature - at a configurable cadence,
over weeks, with realistic load and weather behaviour and optional injected
faults carrying ground-truth labels.

Why this exists: the hackathon dataset only arrives on the day. Building the
whole analytics stack against a simulator means the real file is a swap, not a
rebuild. It also gives something the supplied data almost certainly will not -
labelled faults, so detection rate and false-alarm rate can be measured.

Load and weather are modelled for an Indian LV distribution feeder:
daily double peak, weekday/weekend difference, seasonal and diurnal ambient,
and a cooling-load coupling so hot afternoons draw more current.
"""

from dataclasses import dataclass, field
from typing import List

import numpy as np
import pandas as pd

from ..physics.nameplate import Nameplate
from ..physics.thermal import ThermalModel
from ..physics import unbalance as ub
from . import faults as F


# --------------------------------------------------------------------------- #
# Profiles
# --------------------------------------------------------------------------- #
def daily_load_shape(hours_of_day, profile="mixed"):
    """Per-unit load shape across the day, peaking near 1.0."""
    h = np.asarray(hours_of_day, float)

    def bump(centre, width, height):
        return height * np.exp(-0.5 * ((h - centre) / width) ** 2)

    if profile == "residential":
        base = 0.34
        shape = base + bump(8.0, 1.8, 0.34) + bump(20.0, 2.2, 0.56) + bump(13.0, 2.5, 0.12)
    elif profile == "commercial":
        base = 0.28
        shape = base + bump(12.0, 3.4, 0.62) + bump(17.0, 2.4, 0.30)
    elif profile == "agricultural":
        base = 0.22
        shape = base + bump(5.0, 1.6, 0.55) + bump(22.0, 2.0, 0.48)
    else:  # mixed feeder
        base = 0.32
        shape = base + bump(9.0, 2.2, 0.32) + bump(13.0, 2.6, 0.26) + bump(20.0, 2.3, 0.50)
    return shape


def ambient_profile(t_hours, day_of_year_start=120, season="summer", lat_warm=True):
    """Ambient temperature: seasonal mean with a diurnal swing, minimum around
    06:00 and maximum around 15:00. Defaults describe Jaipur."""
    t = np.asarray(t_hours, float)
    doy = day_of_year_start + t / 24.0
    seasonal_mean = {"summer": 34.0, "monsoon": 29.0, "winter": 17.0, "spring": 27.0}[season]
    seasonal_swing = {"summer": 9.0, "monsoon": 5.0, "winter": 10.0, "spring": 8.0}[season]
    # slow seasonal wander across the record
    drift = 2.5 * np.sin(2 * np.pi * (doy - 100) / 365.0)
    diurnal = seasonal_swing * np.sin(2 * np.pi * (t % 24.0 - 9.0) / 24.0)
    return seasonal_mean + drift + diurnal


@dataclass
class SimConfig:
    days: int = 30
    sample_minutes: float = 15.0
    load_profile: str = "mixed"
    season: str = "summer"
    peak_load_pu: float = 0.75          # peak load ratio on an ordinary day
    weekend_factor: float = 0.84
    base_unbalance_pct: float = 1.6     # healthy feeders are never perfectly balanced
    voltage_regulation_pct: float = 4.0  # volt drop at full load
    noise_current_pct: float = 0.9
    noise_voltage_pct: float = 0.35
    noise_temp_c: float = 0.25
    seed: int = 42
    start: str = "2026-04-01 00:00"


class TransformerSimulator:
    """Generates labelled operating data for one transformer."""

    def __init__(self, nameplate: Nameplate = None, config: SimConfig = None):
        self.np_ = nameplate or Nameplate()
        self.cfg = config or SimConfig()
        self.rng = np.random.default_rng(self.cfg.seed)

    # ---- helpers ------------------------------------------------------------
    def _time_axis(self):
        n = int(self.cfg.days * 24 * 60 / self.cfg.sample_minutes)
        t_h = np.arange(n) * self.cfg.sample_minutes / 60.0
        idx = pd.date_range(self.cfg.start, periods=n,
                            freq=f"{int(self.cfg.sample_minutes)}min")
        return t_h, idx

    def _base_load(self, t_h, idx):
        shape = daily_load_shape(t_h % 24.0, self.cfg.load_profile)
        weekend = np.isin(idx.dayofweek, [6])          # Sunday
        shape = shape * np.where(weekend, self.cfg.weekend_factor, 1.0)
        # day-to-day variation so no two days are identical
        day_id = (t_h // 24).astype(int)
        day_scale = 1.0 + 0.07 * self.rng.standard_normal(day_id.max() + 2)[day_id]
        # slow random walk for week-scale demand changes
        walk = np.cumsum(self.rng.standard_normal(len(t_h))) * 0.0016
        walk -= walk.mean()
        return shape * day_scale * (1.0 + walk)

    # ---- main ---------------------------------------------------------------
    def generate(self, scenario="healthy", custom_faults: List[F.Fault] = None):
        cfg, np_ = self.cfg, self.np_
        flist = list(custom_faults) if custom_faults is not None else \
            list(F.SCENARIOS.get(scenario, []))

        t_h, idx = self._time_axis()
        n = len(t_h)
        dt_min = cfg.sample_minutes

        ambient_true = ambient_profile(t_h, season=cfg.season)
        load_pu = self._base_load(t_h, idx)

        # hot afternoons pull cooling load
        load_pu = load_pu * (1.0 + 0.014 * np.clip(ambient_true - 32.0, 0, None))
        load_pu = load_pu / np.percentile(load_pu, 99) * cfg.peak_load_pu

        # ---- asset faults that act on load or cooling -----------------------
        cooling_factor = np.ones(n)
        tau_scale = np.ones(n)
        unb_offset = np.zeros(n)
        labels = [[] for _ in range(n)]

        for f in flist:
            s = np.array([f.scale(t) for t in t_h])
            act = s > 0
            if f.kind == "cooling_degradation":
                cooling_factor += s
            elif f.kind == "oil_loss":
                cooling_factor += 0.6 * s
                tau_scale -= 0.5 * s          # less oil, faster response
            elif f.kind == "overload":
                load_pu = np.where(act, np.maximum(load_pu, f.severity), load_pu)
            elif f.kind == "unbalance_growth":
                unb_offset += s
            if f.family == "asset":
                for i in np.where(act)[0]:
                    labels[i].append(f.kind)

        # ---- split into phases ---------------------------------------------
        rated_i = np_.rated_current_a
        i_mean = load_pu * rated_i
        u = cfg.base_unbalance_pct / 100.0
        wob = u * np.array([1.0, -0.55, -0.45])            # healthy standing unbalance
        ir = i_mean * (1.0 + wob[0] + 0.3 * unb_offset)
        iy = i_mean * (1.0 + wob[1] + 0.7 * unb_offset)
        ib = i_mean * (1.0 + wob[2] - 1.0 * unb_offset)

        # ---- voltages: drop with load, mild standing unbalance --------------
        vnom = np_.voltage_nominal_ln_v
        droop = 1.0 - (cfg.voltage_regulation_pct / 100.0) * load_pu
        vr = vnom * droop * (1.0 + 0.004)
        vy = vnom * droop * (1.0 + 0.001)
        vb = vnom * droop * (1.0 - 0.005 - 0.25 * unb_offset)

        # ---- thermal truth ---------------------------------------------------
        an = ub.analyse(ir, iy, ib, vr, vy, vb, rated_i, np_.unbalance_limit_pct)
        K_oil, K_wdg = an["K_oil"], an["K_winding"]

        model = ThermalModel(np_)
        state = model.initial_state(K_oil[0], ambient_true[0])
        top_oil = np.empty(n)
        hotspot = np.empty(n)
        base_tau = np_.tau_oil_h
        for i in range(n):
            model.cooling_factor = cooling_factor[i]
            model.np_.tau_oil_h = base_tau * max(tau_scale[i], 0.3)
            state = model.step(state, K_oil[i], K_wdg[i], ambient_true[i], dt_min)
            top_oil[i] = state.top_oil_c
            hotspot[i] = state.hotspot_c
        model.np_.tau_oil_h = base_tau

        # ---- measurement noise ----------------------------------------------
        def jitter(x, pct=None, abs_=None):
            x = np.asarray(x, float)
            s = (pct / 100.0) * np.abs(x) if pct is not None else np.full(len(x), abs_)
            return x + self.rng.standard_normal(len(x)) * s

        meas = {
            "IR": jitter(ir, cfg.noise_current_pct),
            "IY": jitter(iy, cfg.noise_current_pct),
            "IB": jitter(ib, cfg.noise_current_pct),
            "VR": jitter(vr, cfg.noise_voltage_pct),
            "VY": jitter(vy, cfg.noise_voltage_pct),
            "VB": jitter(vb, cfg.noise_voltage_pct),
            "oil_temp": jitter(top_oil, abs_=cfg.noise_temp_c),
            "ambient_temp": jitter(ambient_true, abs_=cfg.noise_temp_c * 1.5),
        }

        # ---- sensor faults act on the MEASUREMENT only ----------------------
        for f in flist:
            if f.family != "sensor" or f.channel not in meas:
                continue
            ch = meas[f.channel]
            act = np.array([f.active(t) for t in t_h])
            s = np.array([f.scale(t) for t in t_h])
            if f.kind == "sensor_drift":
                ch = ch + s
            elif f.kind == "sensor_bias":
                ch = np.where(act, ch + f.severity, ch)
            elif f.kind == "sensor_stuck":
                first = np.argmax(act) if act.any() else None
                if first is not None:
                    ch = ch.copy()
                    ch[act] = ch[first]
            elif f.kind == "sensor_dead":
                ch = ch.copy()
                ch[act] = np.nan
            meas[f.channel] = ch
            for i in np.where(act)[0]:
                labels[i].append(f"{f.kind}:{f.channel}")

        # ---- assemble --------------------------------------------------------
        df = pd.DataFrame(meas, index=idx)
        df.index.name = "timestamp"
        df.insert(0, "transformer_id", np_.tag)

        truth = pd.DataFrame({
            "true_top_oil_c": top_oil,
            "true_hotspot_c": hotspot,
            "true_ambient_c": ambient_true,
            "true_load_pu": K_oil,
            "true_cooling_factor": cooling_factor,
            "fault_label": [";".join(l) if l else "none" for l in labels],
            "is_asset_fault": [any(not x.startswith("sensor_") for x in l) for l in labels],
            "is_sensor_fault": [any(x.startswith("sensor_") for x in l) for l in labels],
        }, index=idx)

        return df.round(3), truth.round(4)

    # ---- convenience --------------------------------------------------------
    def to_csv(self, path_data, path_truth=None, **kw):
        df, truth = self.generate(**kw)
        df.to_csv(path_data)
        if path_truth:
            truth.to_csv(path_truth)
        return df, truth


def quick(scenario="showcase", days=30, rating_kva=100, seed=42, **cfg_kw):
    """One-liner used by the demo scripts and the dashboard's sample data."""
    from ..physics.nameplate import preset
    sim = TransformerSimulator(preset(rating_kva),
                               SimConfig(days=days, seed=seed, **cfg_kw))
    return sim.generate(scenario=scenario)

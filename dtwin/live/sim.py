"""
Step-by-step physics for the live stream: one sample at a time, the same load,
weather, thermal and fault model as dtwin/simulate/generator.py, written as a
recursion instead of over whole arrays.

Faults change the PHYSICS (or, for sensor faults, the MEASUREMENT); the
analytics that receive these samples are never told what was injected.
"""

import dataclasses
import math
from datetime import datetime, timedelta

import numpy as np

from ..physics.nameplate import Nameplate, preset
from ..physics.thermal import ThermalModel, ThermalState
from ..physics import unbalance as ub
from ..simulate.generator import ambient_profile, daily_load_shape

ASSET_KINDS = {"cooling_degradation", "oil_loss", "overload", "unbalance_growth"}
SENSOR_KINDS = {"sensor_drift", "sensor_stuck", "sensor_dead", "sensor_bias"}
CHANNELS = ["IR", "IY", "IB", "VR", "VY", "VB", "oil_temp", "ambient_temp"]


@dataclasses.dataclass
class Fault:
    """One injected fault. start is set to the simulator's clock when it first
    sees the fault; progress ramps 0 -> 1 over ramp_h."""
    id: str
    kind: str
    severity: float
    ramp_h: float = 0.0
    channel: str = None
    start: datetime = None

    def progress(self, t):
        if self.start is None or t < self.start:
            return 0.0
        if self.ramp_h <= 0:
            return 1.0
        return min((t - self.start).total_seconds() / 3600.0 / self.ramp_h, 1.0)


class LiveSimulator:
    def __init__(self, nameplate: Nameplate = None, start: datetime = None, interval_min=15.0,
                 seed=7, season="summer", peak_load_pu=0.75, noise_current_pct=0.9,
                 noise_voltage_pct=0.35, noise_temp_c=0.25, base_unbalance_pct=1.6):
        self.np_ = nameplate or preset(100)
        self.dt = timedelta(minutes=interval_min)
        self.t = (start or datetime.now()).replace(second=0, microsecond=0)
        self.rng = np.random.default_rng(seed)
        self.season, self.peak = season, peak_load_pu
        self.noise = (noise_current_pct, noise_voltage_pct, noise_temp_c)
        self.unb = base_unbalance_pct / 100.0
        self.faults = {}
        self.t0_days = self.t.timetuple().tm_yday
        # scale the daily shape so an ordinary day peaks at peak_load_pu
        hours = np.arange(0, 24, 0.25)
        self.k_load = peak_load_pu / float(np.max(daily_load_shape(hours, "mixed")))
        self.day_scale, self.day_of = 1.0, None
        self.walk = 0.0
        self.state = None
        self.stuck_value = {}

    # ---- faults ------------------------------------------------------------
    def set_faults(self, specs):
        """Replace the active fault set with the backend's list (by id)."""
        keep = {}
        for f in specs:
            old = self.faults.get(f["id"])
            keep[f["id"]] = old or Fault(id=f["id"], kind=f["kind"], severity=float(f["severity"]),
                                         ramp_h=float(f.get("ramp_h") or 0.0), channel=f.get("channel"),
                                         start=self.t)
        for fid in set(self.faults) - set(keep):
            self.stuck_value.pop(fid, None)
        self.faults = keep

    # ---- one step ---------------------------------------------------------------
    def _load(self, t):
        if self.day_of != t.date():
            self.day_of = t.date()
            self.day_scale = 1.0 + 0.07 * self.rng.standard_normal()
        self.walk = 0.995 * self.walk + 0.0016 * self.rng.standard_normal()
        h = t.hour + t.minute / 60.0
        k = self.k_load * float(daily_load_shape(np.array([h]), "mixed")[0]) * self.day_scale * (1 + self.walk)
        if t.weekday() == 6:
            k *= 0.84
        return k

    def _ambient(self, t):
        hours_from_start = (t - datetime(t.year, 1, 1)).total_seconds() / 3600.0 - 120 * 24
        return float(ambient_profile(np.array([hours_from_start]), day_of_year_start=120,
                                     season=self.season)[0])

    def step(self):
        """Advance one interval and return the measured sample."""
        self.t += self.dt
        t = self.t
        np_ = self.np_
        amb = self._ambient(t)
        load = self._load(t) * (1.0 + 0.014 * max(amb - 32.0, 0.0))

        cooling, tau_scale, unb_off = 1.0, 1.0, 0.0
        for f in self.faults.values():
            s = f.severity * f.progress(t)
            if f.kind == "cooling_degradation":
                cooling += s
            elif f.kind == "oil_loss":
                cooling += 0.6 * s
                tau_scale -= 0.5 * s
            elif f.kind == "overload" and f.progress(t) > 0:
                load = max(load, f.severity * (0.85 + 0.15 * f.progress(t)))
            elif f.kind == "unbalance_growth":
                unb_off += s

        i_mean = load * np_.rated_current_a
        wob = self.unb * np.array([1.0, -0.55, -0.45])
        ir = i_mean * (1.0 + wob[0] + 0.3 * unb_off)
        iy = i_mean * (1.0 + wob[1] + 0.7 * unb_off)
        ib = i_mean * (1.0 + wob[2] - 1.0 * unb_off)
        vnom = np_.voltage_nominal_ln_v
        droop = 1.0 - 0.04 * load
        vr, vy, vb = vnom * droop * 1.004, vnom * droop * 1.001, vnom * droop * (1 - 0.005 - 0.25 * unb_off)

        an = ub.analyse([ir], [iy], [ib], [vr], [vy], [vb], np_.rated_current_a, np_.unbalance_limit_pct)
        k_oil, k_wdg = float(an["K_oil"][0]), float(an["K_winding"][0])
        model = ThermalModel(dataclasses.replace(np_, tau_oil_h=np_.tau_oil_h * max(tau_scale, 0.3)),
                             cooling_factor=cooling)
        if self.state is None:
            self.state = model.initial_state(k_oil, amb)
        self.state = model.step(self.state, k_oil, k_wdg, amb, self.dt.total_seconds() / 60.0)

        nc, nv, nt = self.noise
        g = self.rng.standard_normal
        meas = {"IR": ir * (1 + nc / 100 * g()), "IY": iy * (1 + nc / 100 * g()), "IB": ib * (1 + nc / 100 * g()),
                "VR": vr * (1 + nv / 100 * g()), "VY": vy * (1 + nv / 100 * g()), "VB": vb * (1 + nv / 100 * g()),
                "oil_temp": self.state.top_oil_c + nt * g(), "ambient_temp": amb + 1.5 * nt * g()}

        for f in self.faults.values():                   # sensor faults: the measurement only
            if f.kind not in SENSOR_KINDS or f.channel not in meas or f.progress(t) <= 0:
                continue
            if f.kind == "sensor_drift":
                meas[f.channel] += f.severity * f.progress(t)
            elif f.kind == "sensor_bias":
                meas[f.channel] += f.severity
            elif f.kind == "sensor_stuck":
                self.stuck_value.setdefault(f.id, meas[f.channel])
                meas[f.channel] = self.stuck_value[f.id]
            elif f.kind == "sensor_dead":
                meas[f.channel] = None

        return {"timestamp": t.isoformat(timespec="seconds"), "transformer_id": np_.tag,
                **{k: (None if v is None else round(float(v), 3)) for k, v in meas.items()}}

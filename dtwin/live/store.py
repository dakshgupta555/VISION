"""
The live observed state: a buffer of samples and the existing analytics run on
it causally (past data only), after every sample.

Two things are kept strictly apart:

  samples   what was measured (or simulated as a measurement). The ONLY input
            to the analytics: analyse() receives a DataFrame of samples and a
            nameplate, nothing else.
  faults    the injection list. Read by the simulator process so it can change
            its physics; never passed to the analytics.

Freshness is evaluated when a client asks, from the wall-clock age of the last
sample against the expected period (declared by the source, or the median
interval between arrivals). No sample for 3 periods -> stale. A sample that
arrives with a channel missing is a different thing: a dead channel.
"""

import threading
import time
import uuid
from datetime import datetime

import numpy as np
import pandas as pd

from .. import ingest
from ..analytics.validation import validate
from ..physics.nameplate import preset
from .sim import ASSET_KINDS, CHANNELS, SENSOR_KINDS

CONTRACT_FIELDS = {"timestamp", "transformer_id", "source", *CHANNELS}
SOURCES = {"simulator", "sensor"}
MAX_BUFFER_DAYS = 7.0
HISTORY_H = 48.0                   # what a prewarm supplies; below it the dashboard says so
SESSION = uuid.uuid4().hex[:12]    # one per backend process: a change tells the source to re-prewarm
STALE_PERIODS = 3
DEFAULT_SPEED = 120.0             # simulated seconds per real second
OVERLOAD_MAX_PU = 1.5             # injection cap: beyond it the thermal model is far outside C57.91's range


def analyse(samples: pd.DataFrame, rating_kva: float, source: str, tag: str = "DT-001"):
    """The existing engine on a buffer of samples. Takes samples and the
    nameplate only: it cannot see what, if anything, was injected."""
    from ..api.engine import Twin
    np_ = preset(rating_kva)
    np_.tag = tag
    absent = [c for c in CHANNELS if samples[c].isna().all()]
    rows, _, _ = validate(samples, np_, absent=absent)
    rep = ingest.Report(source=f"live: {source}", encoding="live", separator="")
    d = samples.index.to_series().diff().dt.total_seconds().dropna() / 60.0
    rep.cadence = {"median_min": round(float(d.median()), 3) if len(d) else None, "regular": True}
    rep.nameplate = {"rating_kva": rating_kva, "how": "live configuration", "tag": tag}
    rep.rows = {"read": len(samples), "kept": len(samples), "dropped": {}, "moved": 0}
    rep.absent = absent
    tw = Twin("live", np_, samples, rows, report=rep, absent=tuple(absent))
    tw._derive()
    tw.alerts = tw._build_alerts()
    return tw


class LiveStore:
    def __init__(self, rating_kva=100.0, clock=time.time):
        self.rating_kva = rating_kva
        self.clock = clock
        self.lock = threading.RLock()
        self.version = 0
        self.reset()

    def reset(self):
        with self.lock:
            self.rows = {}                 # timestamp -> sample dict
            self.source = None
            self.tag = "DT-001"
            self.arrivals = []             # wall-clock arrival times
            self.declared_period_s = None
            self.prewarm_until = None
            self.twin = None
            self.analysis_ms = None
            self.analysis_error = None
            self.faults = {}               # injection list - simulator only
            self.paused = False
            self.speed = DEFAULT_SPEED
            self.version += 1

    # ---- ingest -----------------------------------------------------------------
    def add(self, samples, prewarm=False, period_s=None):
        """Append samples (dicts in the live contract), then re-run the
        analytics once. Returns the number accepted.

        A malformed sample (bad source, timestamp or number) raises ValueError
        and nothing in the batch is stored. A well-formed sample is ALWAYS
        stored: if the analytics then fail, the error is kept in
        analysis_error and the previous twin stays in place."""
        parsed = []
        for s in samples:
            if not isinstance(s, dict):
                raise ValueError("each sample must be a JSON object")
            src = s.get("source", "simulator")
            if src not in SOURCES:
                raise ValueError(f"source must be one of {sorted(SOURCES)}")
            if "timestamp" not in s:
                raise ValueError("timestamp missing")
            try:
                ts = pd.Timestamp(s["timestamp"])
                vals = {c: (np.nan if s.get(c) is None else float(s[c])) for c in CHANNELS}
            except (TypeError, ValueError) as e:
                raise ValueError(str(e)) from None
            if ts is pd.NaT:
                raise ValueError("timestamp not a date")
            if ts.tzinfo is not None:
                ts = ts.tz_localize(None)
            parsed.append((ts, vals, src, s.get("transformer_id")))
        with self.lock:
            for ts, vals, src, tag in parsed:
                self.rows[ts] = vals
                self.source = src
                self.tag = tag or self.tag
            n = len(parsed)
            if not n:
                return 0
            if prewarm:
                self.prewarm_until = max(self.rows)
            else:
                self.arrivals = (self.arrivals + [self.clock()])[-20:]
            if period_s:
                self.declared_period_s = float(period_s)
            self._trim()
            self.version += 1
            if len(self.rows) < 2:
                self.analysis_error = "insufficient history: fewer than 2 samples"
                return n
            t0 = time.perf_counter()
            try:
                self.twin = analyse(self.frame(), self.rating_kva, self.source, self.tag)
                self.analysis_error = None
            except Exception as e:                       # keep the sample, report, keep serving
                self.analysis_error = f"{type(e).__name__}: {e}"
            self.analysis_ms = round(1000 * (time.perf_counter() - t0), 1)
            self.version += 1
        return n

    def history(self):
        """How much of the 48 h the detectors want is in the buffer."""
        need = None
        if len(self.rows) >= 2:
            idx = sorted(self.rows)
            step_s = float(np.median(np.diff(np.array(idx, dtype="datetime64[ns]")).astype("timedelta64[s]").astype(float)))
            if step_s > 0:
                need = int(round(HISTORY_H * 3600 / step_s))
        n = len(self.rows)
        return {"n": n, "need": need, "rebuilding": need is None or n < need}

    def _trim(self):
        if not self.rows:
            return
        newest = max(self.rows)
        cutoff = newest - pd.Timedelta(days=MAX_BUFFER_DAYS)
        for k in [k for k in self.rows if k < cutoff]:
            del self.rows[k]

    def frame(self):
        df = pd.DataFrame.from_dict(self.rows, orient="index")[CHANNELS].sort_index()
        df.index = pd.DatetimeIndex(df.index).astype("datetime64[ns]")
        df.index.name = "timestamp"
        return df

    # ---- freshness ------------------------------------------------------------------
    def period_s(self):
        if self.declared_period_s:
            return self.declared_period_s
        if len(self.arrivals) >= 3:
            return float(np.median(np.diff(self.arrivals)))
        return None

    def freshness(self):
        with self.lock:
            if not self.arrivals and self.twin is None:
                return {"has_data": False, "stale": True, "reason": "no data received",
                        "session": SESSION, "history": self.history(), "analysis_error": self.analysis_error}
            last = self.arrivals[-1] if self.arrivals else None
            age = None if last is None else self.clock() - last
            per = self.period_s()
            stale = bool(age is not None and per is not None and age > STALE_PERIODS * per) or last is None
            reason = None
            if last is None:
                reason = "only prewarm history so far; no live sample yet"
            elif stale:
                reason = f"no recent data: last sample {age:.0f} s ago, expected every {per:.1f} s"
            return {"has_data": True, "stale": stale, "reason": reason,
                    "sample_age_s": None if age is None else round(age, 1),
                    "period_s": None if per is None else round(per, 2),
                    "source": self.source,
                    "source_label": {"simulator": "Simulated: physics model",
                                     "sensor": "Measured: sensor"}.get(self.source, "unknown"),
                    "last_sample": None if not self.rows else max(self.rows).isoformat(),
                    "prewarm_until": None if self.prewarm_until is None else self.prewarm_until.isoformat(),
                    "n_samples": len(self.rows), "analysis_ms": self.analysis_ms,
                    "analysis_error": self.analysis_error, "history": self.history(), "session": SESSION,
                    "speed": self.speed, "paused": self.paused, "version": self.version}

    # ---- injection list (simulator only) -------------------------------------------
    def inject(self, kind, severity, ramp_h=0.0, channel=None):
        if kind not in ASSET_KINDS | SENSOR_KINDS:
            raise ValueError(f"unknown fault kind {kind!r}")
        if kind == "overload" and not 0 < float(severity) <= OVERLOAD_MAX_PU:
            raise ValueError(f"overload severity must be above 0 and at most {OVERLOAD_MAX_PU} pu")
        if kind in SENSOR_KINDS and channel not in CHANNELS:
            raise ValueError(f"{kind} needs a channel, one of {CHANNELS}")
        if kind == "unbalance_growth":
            channel = None
        fid = uuid.uuid4().hex[:8]
        with self.lock:
            self.faults[fid] = {"id": fid, "kind": kind, "severity": float(severity),
                                "ramp_h": float(ramp_h or 0.0), "channel": channel,
                                "family": "asset" if kind in ASSET_KINDS else "sensor",
                                "injected_at_wall": datetime.now().isoformat(timespec="seconds"),
                                "injected_at_sample": None if not self.rows else max(self.rows).isoformat()}
            self.version += 1
        return self.faults[fid]

    def clear(self, fid=None):
        with self.lock:
            if fid is None:
                self.faults.clear()
            else:
                self.faults.pop(fid, None)
            self.version += 1

    def sim_config(self):
        with self.lock:
            return {"faults": list(self.faults.values()), "paused": self.paused, "speed": self.speed,
                    "session": SESSION}

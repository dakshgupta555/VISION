"""
The network backend's observed state. It knows only what gateways forwarded.

Three fields per transformer, kept apart on purpose:

  health        the twin's own result for THIS unit, from THIS unit's samples
                (the existing engine, dtwin/live/store.py analyse, imported).
                When the unit is not reporting it is the last known value and
                is marked unverified; it is never shown as healthy.
  confidence    how far the diagnosis can be trusted now: none / reduced / high,
                with the reason (no data, stale link, history still filling,
                an untrusted channel, no ambient).
  comms         online / stale / offline, from missed reporting intervals on the
                network clock. Lost communication is OFFLINE or STALE, never a
                failure of the transformer.

No cohort or neighbour ever fills a gap: each unit's twin sees its own buffer
and nothing else.

The network clock is the newest simulated time any gateway has reported. If no
gateway reports at all for NO_UPLINK_WALL_S of wall time, every unit is stale:
the backend cannot see the network.
"""

import threading
import time
import uuid
from collections import deque
from datetime import datetime

import numpy as np
import pandas as pd

from dtwin.live.sim import ASSET_KINDS, CHANNELS, SENSOR_KINDS
from dtwin.live.store import analyse

HISTORY_SAMPLES = 192            # 48 h at 15 min: the 24 h rolling fit and persistence are ready
MAX_BUFFER = 4 * 24 * 4          # four days of 15-min samples per unit
NO_UPLINK_WALL_S = 12.0
SNAPSHOTS = 600
OVERLOAD_MAX_PU = 1.5


def _iso(x):
    return None if x is None else pd.Timestamp(x).isoformat()


class Device:
    def __init__(self, asset):
        self.asset = asset
        self.rows = {}               # sample time -> channels
        self.seqs = set()
        self.last_seq = 0
        self.received = self.lost = self.duplicates = self.rejected = self.late = 0
        self.last_seen = None        # newest sample time received (sim)
        self.last_wall = None
        self.last_gateway = None
        self.version = 0
        self.twin = None
        self.twin_version = -1
        self.state = None            # twin.state() of the newest analysis
        self.analysis_error = None
        self.analysis_ms = None
        self.comm = "no data"
        self.comm_since = None
        self.level = None
        self.last_packet = None      # hop stamps of the newest live packet
        self.link = deque(maxlen=20)  # (rssi, snr) of this node's own recent packets, SIMULATED


class NetworkStore:
    def __init__(self, fleet, clock=time.time):
        self.fleet = fleet
        self.wall = clock
        self.lock = threading.RLock()
        self.session = uuid.uuid4().hex[:8]     # a restarted backend makes the simulator re-send history
        self.reset()

    # ------------------------------------------------------------------ demo state
    def reset(self):
        with getattr(self, "lock", threading.RLock()):
            self.devices = {a.id: Device(a) for a in self.fleet.monitored}
            self.gw = {g.id: {"last_sim": None, "last_wall": None, "forwarded": 0, "last_batch": 0,
                              "waiting": 0, "capacity": g.capacity, "status": "no data"}
                       for g in self.fleet.gateways.values() if g.existing}
            self.clock_sim = None
            self.last_uplink_wall = None
            self.events = deque(maxlen=500)
            self.event_seq = 0
            self.snapshots = deque(maxlen=SNAPSHOTS)
            self.demo = {"faults": {}, "comm_cut": [], "node_down": [], "gateway_down": [],
                         "packet_loss": 0.0, "capacity": {}, "paused": False,
                         "epoch": getattr(self, "demo", {}).get("epoch", 0) + 1}
            self.version = 0

    def interval_min(self):
        return float(self.fleet.radio["reporting_interval_min"])

    def log(self, kind, text, level="info", device=None, gateway=None, chain=None, sim=None):
        self.event_seq += 1
        self.events.append({"n": self.event_seq, "wall": self.wall(), "sim": sim or _iso(self.clock_sim),
                            "kind": kind, "level": level, "device": device, "gateway": gateway,
                            "text": text, "chain": chain})

    # ---------------------------------------------------------------- uplinks
    def uplink(self, up):
        """One gateway's forwarded batch (or heartbeat). Raises ValueError for a
        malformed envelope; a malformed packet inside is counted and skipped."""
        rx = self.wall()
        if not isinstance(up, dict):
            raise ValueError("uplink must be a JSON object")
        gid = up.get("gateway_id")
        if gid not in self.gw:
            raise ValueError(f"unknown gateway {gid!r}")
        try:
            sim_t = pd.Timestamp(up["sim_time"]).tz_localize(None)
        except (KeyError, TypeError, ValueError):
            raise ValueError("uplink needs a valid sim_time") from None
        pk = up.get("packets") or []
        if not isinstance(pk, list):
            raise ValueError("packets must be a list")
        prewarm = bool(up.get("prewarm"))
        bad = []
        with self.lock:
            g = self.gw[gid]
            g.update(last_sim=sim_t, last_wall=rx, waiting=int(up.get("waiting") or 0), last_batch=len(pk),
                     capacity=int(up.get("capacity") or g["capacity"]))
            if g["status"] != "online":
                if g["status"] == "offline":
                    self.log("gateway", f"{gid} reporting again", "ok", gateway=gid)
                g["status"] = "online"
            self.clock_sim = sim_t if self.clock_sim is None else max(self.clock_sim, sim_t)
            self.last_uplink_wall = rx
            replayed = {}
            for p in pk:
                why = self._accept(p, gid, rx, prewarm)
                if why:
                    bad.append(why)
                    continue
                g["forwarded"] += 1
                if p.get("replayed"):
                    replayed[p["device_id"]] = replayed.get(p["device_id"], 0) + 1
            for dev, k in replayed.items():
                self.log("replay", f"{dev}: {k} buffered packet{'s' if k > 1 else ''} received late via {gid}",
                         "info", device=dev, gateway=gid)
            if prewarm and pk:
                devs = sorted({p.get("device_id") for p in pk if isinstance(p, dict)})
                self.log("prewarm", f"{gid}: {len(pk) - len(bad)} history packets from {', '.join(devs)} "
                         "(48 h, sent at start so each twin's 24 h fit is ready)", "info", gateway=gid)
            for why in bad[:3]:
                self.log("reject", f"packet rejected: {why}", "warn", gateway=gid)
            self.version += 1
        return {"accepted": len(pk) - len(bad), "rejected": len(bad), "reasons": bad[:5]}

    def _accept(self, p, gid, rx, prewarm):
        if not isinstance(p, dict):
            return "not an object"
        did = p.get("device_id")
        d = self.devices.get(did)
        if d is None:
            return f"unknown device {did!r}"
        seq = p.get("seq")
        if not isinstance(seq, int) or seq < 1:
            d.rejected += 1
            return f"{did}: sequence number must be a positive integer"
        try:
            ts = pd.Timestamp(p["ts"]).tz_localize(None)
        except (KeyError, TypeError, ValueError):
            d.rejected += 1
            return f"{did}: bad timestamp"
        pay = p.get("payload")
        if not isinstance(pay, dict):
            d.rejected += 1
            return f"{did}: payload missing"
        try:
            vals = {c: (np.nan if pay.get(c) is None else float(pay[c])) for c in CHANNELS}
        except (TypeError, ValueError):
            d.rejected += 1
            return f"{did}: non-numeric reading"
        if seq in d.seqs:
            d.duplicates += 1
            return None                                  # a duplicate is not an error, just ignored
        if gid != d.asset.gateway:
            self.log("route", f"{did} heard by {gid}, not its home gateway {d.asset.gateway}; kept", "info",
                     device=did, gateway=gid)
        if seq > d.last_seq + 1 and not prewarm:
            d.lost += seq - d.last_seq - 1
        elif seq < d.last_seq and d.lost > 0:
            d.lost -= 1                                  # counted missing, arrived late (queued behind an alarm)
        d.seqs.add(seq)
        d.last_seq = max(d.last_seq, seq)
        d.rows[ts] = vals
        d.received += 1
        d.late += bool(p.get("replayed"))
        if len(d.rows) > MAX_BUFFER:
            for k in sorted(d.rows)[:len(d.rows) - MAX_BUFFER]:
                del d.rows[k]
        if d.last_seen is None or ts >= d.last_seen:
            d.last_seen, d.last_gateway = ts, gid
        d.last_wall = rx
        if p.get("rssi_dbm") is not None and not prewarm:
            d.link.append((float(p["rssi_dbm"]), float(p.get("snr_db") or 0.0)))
        d.version += 1
        if not prewarm:
            d.last_packet = {"seq": seq, "ts": _iso(ts), "priority": p.get("priority", "normal"),
                             "chain": [{"hop": "node", "who": did, "wall": p.get("tx_wall")},
                                       {"hop": "gateway", "who": gid, "wall": p.get("gw_wall")},
                                       {"hop": "backend", "who": "backend", "wall": rx}]}
            if p.get("priority") == "alarm":
                self.log("edge_alarm", f"{did} sent a priority alarm ({p.get('edge_reason')})", "alarm",
                         device=did, gateway=gid, chain=d.last_packet["chain"], sim=_iso(ts))
        return None

    # ----------------------------------------------------------- analysis
    def frame(self, d):
        df = pd.DataFrame.from_dict(d.rows, orient="index")[CHANNELS].sort_index()
        df.index = pd.DatetimeIndex(df.index).astype("datetime64[ns]")
        df.index.name = "timestamp"
        return df

    def analyse_device(self, did):
        """Run the existing twin on this unit's own buffer. Nothing from any
        other unit is read."""
        with self.lock:
            d = self.devices[did]
            if d.version == d.twin_version or len(d.rows) < 2:
                return False
            df, ver = self.frame(d), d.version
        t0 = time.perf_counter()
        try:
            tw = analyse(df, d.asset.rating_kva, "simulator", tag=did)
            st = tw.state()
            err = None
        except Exception as e:                            # keep the last good twin and say why
            tw, st, err = None, None, f"{type(e).__name__}: {e}"
        with self.lock:
            d.analysis_ms = round(1000 * (time.perf_counter() - t0), 1)
            d.twin_version = ver
            d.analysis_error = err
            if tw is not None:
                d.twin, d.state = tw, st
                self._diagnosis_events(d)
        return True

    def _diagnosis_events(self, d):
        lvl = level_of(d.state)
        if lvl == d.level:
            return
        prev, d.level = d.level, lvl
        if prev is None and lvl == "ok":
            return
        a = (d.state.get("alerts_live") or [None])[0]
        chain = None
        if d.last_packet:
            chain = d.last_packet["chain"] + [{"hop": "twin", "who": "twin", "wall": self.wall()}]
        if lvl == "ok":
            self.log("diagnosis", f"{d.asset.id}: back to normal", "ok", device=d.asset.id,
                     gateway=d.last_gateway, chain=chain, sim=d.state["timestamp"])
        else:
            what = a["what"] if a else {"warn": "hot-spot heading for its limit"}.get(lvl, lvl)
            self.log("diagnosis", f"{d.asset.id}: {what}", lvl, device=d.asset.id, gateway=d.last_gateway,
                     chain=chain, sim=d.state["timestamp"])

    def analyse_all(self):
        n = 0
        for did in list(self.devices):
            n += self.analyse_device(did)
        return n

    # --------------------------------------------------------------- comms
    def comms(self, d):
        """online / stale / offline from missed intervals on the network clock."""
        r = self.fleet.radio
        if d.last_seen is None:
            return {"status": "no data", "missed": None, "reason": "nothing received yet"}
        blind = self.last_uplink_wall is None or self.wall() - self.last_uplink_wall > NO_UPLINK_WALL_S
        missed = int(round((self.clock_sim - d.last_seen).total_seconds() / 60 / self.interval_min()))
        if blind:
            return {"status": "stale", "missed": missed,
                    "reason": f"no gateway has reported for over {NO_UPLINK_WALL_S:.0f} s: the backend cannot see the network"}
        if missed >= r["offline_after_missed"]:
            st = "offline"
        elif missed >= r["stale_after_missed"]:
            st = "stale"
        else:
            st = "online"
        reason = None if st == "online" else f"{missed} reporting intervals missed (last packet {_iso(d.last_seen)[11:16]})"
        return {"status": st, "missed": missed, "reason": reason}

    def gateway_status(self, gid):
        g = self.gw[gid]
        if g["last_sim"] is None:
            return "no data"
        missed = (self.clock_sim - g["last_sim"]).total_seconds() / 60 / self.interval_min()
        blind = self.wall() - (self.last_uplink_wall or 0) > NO_UPLINK_WALL_S
        if blind or missed >= self.fleet.radio["stale_after_missed"]:
            return "offline" if missed >= self.fleet.radio["offline_after_missed"] or blind else "stale"
        return "online"

    def tick(self):
        """Comms transitions and a replay snapshot. Call after uplinks."""
        with self.lock:
            for gid, g in self.gw.items():
                st = self.gateway_status(gid)
                if st != g["status"] and g["status"] != "no data":
                    if st != "online":
                        self.log("gateway", f"{gid} {st}: no uplink for "
                                 f"{self.fleet.radio['stale_after_missed']}+ intervals", "warn", gateway=gid)
                g["status"] = st
            for d in self.devices.values():
                c = self.comms(d)["status"]
                if c != d.comm:
                    if d.comm != "no data":
                        tone = "ok" if c == "online" else "warn" if c == "stale" else "info"
                        msg = {"online": "reporting again", "stale": "STALE: packets overdue",
                               "offline": "OFFLINE: no packets; health shown is last known, unverified"}[c]
                        self.log("comms", f"{d.asset.id} {msg}", tone, device=d.asset.id, gateway=d.asset.gateway)
                    d.comm, d.comm_since = c, _iso(self.clock_sim)
            if self.clock_sim is not None and (not self.snapshots or self.snapshots[-1]["sim"] != _iso(self.clock_sim)):
                self.snapshots.append({"sim": _iso(self.clock_sim), "wall": self.wall(),
                                       "devices": {k: self.summary(k, brief=True) for k in self.devices},
                                       "gateways": self.gateways_json(), "event_n": self.event_seq})

    # --------------------------------------------------------------- views
    def confidence(self, d, comm):
        st = d.state
        if comm["status"] in ("offline", "no data") or st is None:
            return {"level": "none", "reason": "not reporting" if st else "no analysis yet"}
        reasons = []
        if comm["status"] == "stale":
            reasons.append("packets overdue")
        if len(d.rows) < HISTORY_SAMPLES:
            reasons.append(f"history filling, {len(d.rows)} of {HISTORY_SAMPLES} samples")
        sus = [c for c in (st["status"].get("suspect_channels") or "").split(",") if c]
        if sus:
            reasons.append(f"untrusted channel: {', '.join(sus)}")
        if not st["data_source"].get("thermal_available", True):
            reasons.append("no ambient: thermal diagnosis unavailable")
        return {"level": "reduced" if reasons else "high", "reason": "; ".join(reasons) or "full history, every channel trusted"}

    def summary(self, did, brief=False):
        d = self.devices[did]
        comm = self.comms(d)
        st = d.state
        verified = comm["status"] == "online" and st is not None
        lvl = level_of(st) if st else None
        out = {"id": did, "gateway": d.asset.gateway, "rating_kva": d.asset.rating_kva,
               "health": {"index": None if st is None else st["health"]["index"], "level": lvl,
                          "verified": verified, "as_of": None if st is None else st["timestamp"],
                          "label": None if st is None else label_of(st, lvl)},
               "comms": {**comm, "last_seen": _iso(d.last_seen), "since": d.comm_since},
               "confidence": self.confidence(d, comm)}
        if brief:
            return out
        out["comms"].update(received=d.received, lost=d.lost, duplicates=d.duplicates, rejected=d.rejected,
                            late=d.late, last_gateway=d.last_gateway, last_packet=d.last_packet)
        out["link_simulated"] = None if not d.link else {
            "rssi_dbm": d.link[-1][0], "snr_db": d.link[-1][1],
            "rssi_mean_dbm": round(sum(x[0] for x in d.link) / len(d.link), 1),
            "snr_mean_db": round(sum(x[1] for x in d.link) / len(d.link), 1), "packets": len(d.link),
            "label": "simulated: link-budget mean + seeded shadowing and fading, not a measurement"}
        out["analysis"] = {"ms": d.analysis_ms, "error": d.analysis_error, "samples": len(d.rows)}
        if st:
            hs = st["countdown"]["hotspot"]
            out["twin"] = {"alerts": [{k: a[k] for k in ("what", "why", "action", "level", "family", "kind", "start")}
                                      for a in st["alerts_live"]],
                           "recommendations": st["recommendations"][:2],
                           "hotspot_c": st["readings"]["hotspot_c"], "load_pct": st["readings"]["load_pct"],
                           "oil_c": st["readings"]["oil_c"], "ambient_c": st["readings"]["ambient_c"],
                           "minutes_to_limit": hs["minutes"], "above_limit": hs["already_above"],
                           "shed_pct": hs["shed_pct"], "limit_c": hs["limit_c"]}
        return out

    def gateways_json(self):
        out = []
        for gid, g in self.gw.items():
            gw = self.fleet.gateways[gid]
            nodes = [a.id for a in self.fleet.monitored if a.gateway == gid]
            cap = int(self.demo["capacity"].get(gid, g["capacity"]))
            out.append({"id": gid, "status": self.gateway_status(gid) if self.clock_sim is not None else "no data",
                        "nodes": nodes, "capacity": cap, "used": len(nodes), "last_batch": g["last_batch"],
                        "waiting": g["waiting"], "forwarded": g["forwarded"], "last_sim": _iso(g["last_sim"]),
                        "down_by_demo": gid in self.demo["gateway_down"]})
        return out

    # ---------------------------------------------------------------- demo
    def inject(self, did, kind, severity, ramp_h=0.0, channel=None):
        if did not in self.devices:
            raise ValueError(f"{did!r} is not a monitored transformer")
        if kind not in ASSET_KINDS | SENSOR_KINDS:
            raise ValueError(f"unknown fault kind {kind!r}")
        if kind == "overload" and not 0 < float(severity) <= OVERLOAD_MAX_PU:
            raise ValueError(f"overload severity must be above 0 and at most {OVERLOAD_MAX_PU} pu")
        if kind in SENSOR_KINDS and channel not in CHANNELS:
            raise ValueError(f"{kind} needs a channel, one of {CHANNELS}")
        with self.lock:
            lst = self.demo["faults"].setdefault(did, [])
            fid = f"{did}-{len(lst) + 1}"
            lst.append({"id": fid, "kind": kind, "severity": float(severity), "ramp_h": float(ramp_h or 0),
                        "channel": channel})
            self.log("demo", f"Demo: {kind.replace('_', ' ')} injected into {did}'s physics "
                     "(the backend sees only its packets)", "info", device=did)
            self.version += 1
        return fid

    def set_link(self, did, cut):
        if did not in self.devices:
            raise ValueError(f"{did!r} is not a monitored transformer")
        with self.lock:
            s = set(self.demo["comm_cut"])
            (s.add if cut else s.discard)(did)
            self.demo["comm_cut"] = sorted(s)
            self.log("demo", f"Demo: radio link of {did} {'cut' if cut else 'restored'}", "info", device=did)
            self.version += 1

    def set_gateway(self, gid, down):
        if gid not in self.gw:
            raise ValueError(f"unknown gateway {gid!r}")
        with self.lock:
            s = set(self.demo["gateway_down"])
            (s.add if down else s.discard)(gid)
            self.demo["gateway_down"] = sorted(s)
            self.log("demo", f"Demo: gateway {gid} {'switched off' if down else 'switched on'}", "info", gateway=gid)
            self.version += 1

    def sim_config(self):
        with self.lock:
            return {**self.demo, "session": self.session}


def level_of(st):
    """ok / warn / alarm / sensor, the same rule as the single-unit dashboard's
    status pill: a problem that is only instrument faults is 'sensor'."""
    if st is None:
        return None
    lvl = st["status"]["level"]
    live = st["alerts_live"]
    if lvl == "ok":
        return "warn" if st["countdown"]["hotspot"]["already_above"] else "ok"
    if live and all(a["family"] == "sensor" for a in live):
        return "sensor"
    return "alarm" if lvl == "alarm" else "warn"


def label_of(st, lvl):
    a = (st.get("alerts_live") or [None])[0]
    if a:
        return a["what"]
    return {"ok": "Normal", "warn": "Hot-spot over limit"}.get(lvl, lvl)

"""
The radio side of the simulated network: LoRaWAN star-of-stars.

    node --(one radio hop)--> gateway --(backhaul)--> backend

No multi-hop mesh: a node talks only to its gateway. A gateway forwards each
packet unchanged with its own receive stamp; it never merges, averages or
drops-and-replaces packets from different nodes.

Each monitored transformer runs its OWN physics (the existing step simulator,
dtwin/live/sim.py, imported) with its own nameplate, load seed and ambient.
A fault injected into one node changes only that node's physics.

What can go wrong, all configurable per step (FleetSim.configure):
  packet_loss     probability an uplink is lost in the air (unconfirmed: gone,
                  the backend sees a sequence gap)
  comm_cut        node keeps measuring, its radio link is down: packets wait in
                  the node's buffer and are replayed, oldest first, on reconnect
  node_down       node off (power loss): nothing is measured or sent
  gateway_down    gateway off: its nodes buffer, as for a cut link
  capacity        uplinks a gateway accepts per reporting interval; the rest
                  stay in the node buffers and go next interval. Alarm packets
                  are taken first.
"""

import dataclasses
import time
from collections import deque
from datetime import datetime, timedelta

import numpy as np

from dtwin.live.sim import CHANNELS, LiveSimulator
from dtwin.physics.nameplate import preset

from . import geo

EDGE_RISE_K = 55.0        # node-side alarm: top-oil rise over ambient at its rated value
EDGE_LOAD_PU = 1.0        # node-side alarm: mean phase current above rating


class NetSim(LiveSimulator):
    """The existing step simulator with a per-site ambient offset. Physics is
    inherited unchanged."""

    def __init__(self, *a, ambient_offset_c=0.0, **kw):
        super().__init__(*a, **kw)
        self.ambient_offset_c = float(ambient_offset_c)

    def _ambient(self, t):
        return super()._ambient(t) + self.ambient_offset_c


@dataclasses.dataclass
class Packet:
    device_id: str
    seq: int
    ts: str                      # sample time (simulated clock), ISO
    payload: dict                # the eight channels, None for a missing one
    priority: str = "normal"     # "alarm" packets are sent at once and taken first
    edge_reason: str = None
    tx_wall: float = None        # node transmit, wall clock
    gateway_id: str = None
    gw_wall: float = None        # gateway receive and forward, wall clock
    prewarm: bool = False
    replayed: bool = False       # sent late from the node buffer
    rssi_dbm: float = None       # gateway receive metadata, SIMULATED (link-budget mean + seeded
    snr_db: float = None         # shadowing + fading); never part of the payload

    def to_json(self):
        return dataclasses.asdict(self)


class Node:
    def __init__(self, asset, start, interval_min, buffer_max, link=None):
        np_ = preset(asset.rating_kva)
        np_.tag = asset.id
        sim = asset.sim or {}
        self.id, self.gateway = asset.id, asset.gateway
        self.rated_a = np_.rated_current_a
        self.sim = NetSim(np_, start=start, interval_min=interval_min, seed=int(sim.get("load_seed", 7)),
                          peak_load_pu=float(sim.get("peak_load_pu", 0.75)),
                          ambient_offset_c=float(sim.get("ambient_offset_c", 0.0)))
        self.seq = 0
        self.edge_active = False                   # latched: one priority packet per alarm onset
        self.outbox = deque(maxlen=buffer_max)     # waiting for an acknowledged uplink
        self.dropped_overflow = 0
        self.rng = np.random.default_rng(1000 + int(sim.get("load_seed", 7)))   # this node's air losses only
        # simulated link quality: its own generator, so it never shifts the loss draws
        self.radio_rng = np.random.default_rng(5000 + int(sim.get("load_seed", 7)))
        self.link = None
        if link:
            lb, d_m = link
            shadow = float(np.random.default_rng(9000 + int(sim.get("load_seed", 7))).normal(0, lb["shadowing_sd_db"]))
            self.link = {"mean_rssi": geo.rx_power_dbm(d_m, lb) + shadow, "noise": geo.noise_floor_dbm(lb)}

    def stamp(self, p):
        """Gateway receive metadata for one packet: simulated RSSI and SNR."""
        if not self.link:
            return
        rssi = self.link["mean_rssi"] + float(self.radio_rng.normal(0, 2.0))
        p.rssi_dbm = round(rssi, 1)
        p.snr_db = round(min(rssi - self.link["noise"], 12.0), 1)     # LoRa SNR saturates near +10-12 dB

    def _edge_alarm(self, s):
        cur = [s[c] for c in ("IR", "IY", "IB") if s.get(c) is not None]
        if cur and np.mean(cur) / self.rated_a > EDGE_LOAD_PU:
            return f"load {100 * np.mean(cur) / self.rated_a:.0f} % of rating"
        if s.get("oil_temp") is not None and s.get("ambient_temp") is not None \
                and s["oil_temp"] - s["ambient_temp"] > EDGE_RISE_K:
            return f"oil rise {s['oil_temp'] - s['ambient_temp']:.0f} K over ambient"
        return None

    def measure(self, faults, prewarm=False):
        """One reporting interval: step the physics, queue one packet."""
        self.sim.set_faults(faults)
        s = self.sim.step()
        self.seq += 1
        why = None if prewarm else self._edge_alarm(s)
        onset = bool(why) and not self.edge_active
        self.edge_active = bool(why)
        why = why if onset else None
        p = Packet(self.id, self.seq, s["timestamp"], {c: s[c] for c in CHANNELS},
                   priority="alarm" if why else "normal", edge_reason=why, prewarm=prewarm)
        if len(self.outbox) == self.outbox.maxlen:
            self.dropped_overflow += 1                # oldest sample lost: buffer full
        self.outbox.append(p)
        return p


class FleetSim:
    """Every monitored node and every existing gateway, stepped together on one
    simulated clock. Stepping returns what each gateway forwarded."""

    def __init__(self, fleet, start=None, interval_min=None):
        self.fleet = fleet
        self.interval_min = float(interval_min or fleet.radio["reporting_interval_min"])
        if start is None:
            now = datetime.now().replace(second=0, microsecond=0)
            start = now - timedelta(minutes=now.minute % int(self.interval_min))
        self.t = start
        buf = int(fleet.radio.get("node_buffer_packets", 192))
        lb = fleet.link_budget if (fleet.link_budget or {}).get("sensitivity_dbm") else None
        self.nodes = {a.id: Node(a, start, self.interval_min, buf,
                                 link=(lb, fleet.distance_m(a, fleet.gateways[a.gateway])) if lb else None)
                      for a in fleet.monitored}
        self.gateways = {g.id: g for g in fleet.gateways.values() if g.existing}
        self.cfg = {"faults": {}, "comm_cut": set(), "node_down": set(), "gateway_down": set(),
                    "packet_loss": 0.0, "capacity": {}}

    def configure(self, faults=None, comm_cut=(), node_down=(), gateway_down=(), packet_loss=0.0, capacity=None):
        self.cfg = {"faults": dict(faults or {}), "comm_cut": set(comm_cut), "node_down": set(node_down),
                    "gateway_down": set(gateway_down), "packet_loss": float(packet_loss or 0.0),
                    "capacity": dict(capacity or {})}

    def capacity(self, gid):
        return int(self.cfg["capacity"].get(gid, self.gateways[gid].capacity))

    def step(self, prewarm=False, wall=time.time):
        """Advance one reporting interval. Returns one uplink per gateway that
        is up: {"gateway_id", "sim_time", "packets": [...], "waiting": n}."""
        self.t += timedelta(minutes=self.interval_min)
        for nid, n in self.nodes.items():
            if nid in self.cfg["node_down"]:
                n.sim.t += timedelta(minutes=self.interval_min)     # clock moves on; nothing measured
                continue
            n.measure(self.cfg["faults"].get(nid, []), prewarm=prewarm)
        ups = []
        for gid in self.gateways:
            if gid in self.cfg["gateway_down"]:
                continue
            reach = [n for n in self.nodes.values() if n.gateway == gid
                     and n.id not in self.cfg["comm_cut"] and n.id not in self.cfg["node_down"]]
            # alarms first, then oldest first; one queue per gateway interval
            pending = sorted(((p.priority != "alarm", p.ts, p.device_id, p, n) for n in reach for p in n.outbox),
                             key=lambda x: x[:3])
            cap = self.capacity(gid) if not prewarm else 10 ** 9
            sent = []
            for _, _, _, p, n in pending[:cap]:
                n.outbox.remove(p)                             # acknowledged or lost: it leaves the buffer
                p.tx_wall = wall()
                if not prewarm and n.rng.random() < self.cfg["packet_loss"]:
                    continue                                   # lost in the air: never reaches the gateway
                p.replayed = (not prewarm) and p.ts < self.t.isoformat(timespec="seconds")
                p.gateway_id, p.gw_wall = gid, wall()
                n.stamp(p)
                sent.append(p)
            waiting = sum(len(n.outbox) for n in reach)
            ups.append({"gateway_id": gid, "sim_time": self.t.isoformat(timespec="seconds"),
                        "packets": [p.to_json() for p in sent], "waiting": waiting,
                        "capacity": self.capacity(gid), "prewarm": prewarm})
        return ups

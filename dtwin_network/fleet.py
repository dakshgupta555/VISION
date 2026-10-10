"""
The fleet: monitored and unmonitored transformers, existing and candidate
gateways, read from config_network/fleet.yaml and checked before anything uses
it. A record with no coordinates or no cost is refused with a message naming
the record and the field; nothing is filled in from neighbours or cohorts.
"""

import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

FLEET_YAML = Path(__file__).resolve().parents[1] / "config_network" / "fleet.yaml"


class FleetError(ValueError):
    """The fleet file cannot be used as written. The message says what to fix."""


@dataclass
class Gateway:
    id: str
    name: str
    xy: tuple
    existing: bool
    capacity: int
    radius_km: float
    cost_inr: float
    annual_inr: float


@dataclass
class Asset:
    id: str
    monitored: bool
    gateway: str
    rating_kva: float
    manufacturer: str
    xy: tuple
    meta: dict
    node_cost_inr: float
    node_annual_inr: float
    sim: dict = field(default_factory=dict)

    @property
    def cohort(self):
        return f"{self.manufacturer} {self.rating_kva:g} kVA"


@dataclass
class Fleet:
    district: dict
    radio: dict
    costs: dict
    gateways: dict
    assets: dict

    @property
    def monitored(self):
        return [a for a in self.assets.values() if a.monitored]

    @property
    def unmonitored(self):
        return [a for a in self.assets.values() if not a.monitored]

    def in_range(self, asset, gw, radius_scale=1.0):
        return dist(asset.xy, gw.xy) <= gw.radius_km * radius_scale + 1e-9

    def to_json(self):
        return {
            "district": self.district, "radio": self.radio, "costs": self.costs,
            "gateways": [{"id": g.id, "name": g.name, "xy_km": list(g.xy), "existing": g.existing,
                          "capacity_nodes": g.capacity, "coverage_radius_km": g.radius_km,
                          "cost_inr": g.cost_inr, "annual_inr": g.annual_inr, "coordinates_synthetic": True}
                         for g in self.gateways.values()],
            "assets": [{"id": a.id, "monitored": a.monitored, "gateway": a.gateway, "rating_kva": a.rating_kva,
                        "manufacturer": a.manufacturer, "cohort": a.cohort, "xy_km": list(a.xy),
                        "coordinates_synthetic": True, "metadata": a.meta,
                        "node_cost_inr": a.node_cost_inr, "node_annual_inr": a.node_annual_inr}
                       for a in self.assets.values()],
        }


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _num(rec, key, where, default=None, positive=False):
    v = rec.get(key, default)
    if v is None:
        raise FleetError(f"{where}: '{key}' is missing. Add it to the record, or set a default under 'costs:'.")
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise FleetError(f"{where}: '{key}' must be a number, got {rec.get(key)!r}.") from None
    if not math.isfinite(v) or v < 0 or (positive and v == 0):
        raise FleetError(f"{where}: '{key}' must be {'above' if positive else 'at least'} 0, got {v:g}.")
    return v


def _xy(rec, where):
    xy = rec.get("xy_km")
    if xy is None:
        raise FleetError(f"{where}: coordinates 'xy_km' are missing. Without a position the asset cannot be "
                         "placed on the map or checked against gateway range; add [x, y] in km.")
    if not (isinstance(xy, (list, tuple)) and len(xy) == 2):
        raise FleetError(f"{where}: 'xy_km' must be [x, y] in km, got {xy!r}.")
    try:
        x, y = float(xy[0]), float(xy[1])
    except (TypeError, ValueError):
        raise FleetError(f"{where}: 'xy_km' must hold two numbers, got {xy!r}.") from None
    if not (math.isfinite(x) and math.isfinite(y)):
        raise FleetError(f"{where}: 'xy_km' must hold finite numbers, got {xy!r}.")
    return (x, y)


def parse(cfg: dict) -> Fleet:
    if not isinstance(cfg, dict):
        raise FleetError("fleet file is empty or not a mapping")
    costs = cfg.get("costs") or {}
    radio = cfg.get("radio") or {}
    for k in ("reporting_interval_min", "stale_after_missed", "offline_after_missed"):
        _num(radio, k, "radio", positive=True)
    gws = {}
    for g in cfg.get("gateways") or []:
        where = f"gateway {g.get('id', '?')}"
        if not g.get("id"):
            raise FleetError("a gateway record has no 'id'")
        if g["id"] in gws:
            raise FleetError(f"{where}: duplicate id")
        gws[g["id"]] = Gateway(
            id=g["id"], name=g.get("name", g["id"]), xy=_xy(g, where), existing=bool(g.get("existing")),
            capacity=int(_num(g, "capacity_nodes", where, positive=True)),
            radius_km=_num(g, "coverage_radius_km", where, radio.get("coverage_radius_km"), positive=True),
            cost_inr=_num(g, "cost_inr", where, costs.get("gateway_cost_inr")),
            annual_inr=_num(g, "annual_inr", where, costs.get("gateway_annual_inr")))
    if not any(g.existing for g in gws.values()):
        raise FleetError("no existing gateway: monitored assets need one to reach the backend")
    assets = {}
    for a in cfg.get("assets") or []:
        where = f"asset {a.get('id', '?')}"
        if not a.get("id"):
            raise FleetError("an asset record has no 'id'")
        if a["id"] in assets:
            raise FleetError(f"{where}: duplicate id")
        np_ = a.get("nameplate") or {}
        mon = bool(a.get("monitored"))
        gw = a.get("gateway")
        if mon:
            if gw not in gws or not gws[gw].existing:
                raise FleetError(f"{where}: monitored, so 'gateway' must name an existing gateway, got {gw!r}.")
        assets[a["id"]] = Asset(
            id=a["id"], monitored=mon, gateway=gw if mon else None,
            rating_kva=_num(np_, "rating_kva", f"{where} nameplate", positive=True),
            manufacturer=str(a.get("manufacturer") or "unknown"), xy=_xy(a, where),
            meta=dict(a.get("metadata") or {}),
            node_cost_inr=_num(a, "node_cost_inr", where, costs.get("node_cost_inr")),
            node_annual_inr=_num(a, "node_annual_inr", where, costs.get("node_annual_inr")),
            sim=dict(a.get("sim") or {}))
    if not assets:
        raise FleetError("no assets in the fleet file")
    fl = Fleet(cfg.get("district") or {}, radio, costs, gws, assets)
    for g in gws.values():
        used = sum(1 for a in fl.monitored if a.gateway == g.id)
        if used > g.capacity:
            raise FleetError(f"gateway {g.id}: {used} monitored nodes assigned, capacity is {g.capacity}.")
    for a in fl.monitored:
        if not fl.in_range(a, gws[a.gateway]):
            raise FleetError(f"asset {a.id}: {dist(a.xy, gws[a.gateway].xy):.1f} km from its gateway "
                             f"{a.gateway}, beyond its {gws[a.gateway].radius_km:g} km coverage radius.")
    return fl


def load(path=FLEET_YAML) -> Fleet:
    with open(path) as f:
        return parse(yaml.safe_load(f))

"""
The fleet: monitored and unmonitored transformers, existing and candidate
gateways, read from config_network/fleet.yaml and checked before anything uses
it. A record with no coordinates or no cost is refused with a message naming
the record and the field; nothing is filled in from neighbours or cohorts.

Positions are lat/lon (a synthetic layout near Hoskote, Bengaluru Rural).
Distances are haversine metres (dtwin_network/geo.py). The local x/y km kept on
each record is only for drawing the offline SVG map.
"""

import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import geo

FLEET_YAML = Path(__file__).resolve().parents[1] / "config_network" / "fleet.yaml"


class FleetError(ValueError):
    """The fleet file cannot be used as written. The message says what to fix."""


@dataclass
class Gateway:
    id: str
    name: str
    lat: float
    lon: float
    existing: bool
    capacity: int            # max_nodes
    radius_m: float          # assumed_radius_m
    cost_inr: float          # install_cost_inr
    annual_inr: float
    xy: tuple = (0.0, 0.0)

    @property
    def radius_km(self):
        return self.radius_m / 1000.0

    @property
    def status(self):
        return "existing" if self.existing else "candidate"


@dataclass
class Asset:
    id: str
    monitored: bool
    gateway: str
    rating_kva: float
    manufacturer: str
    lat: float
    lon: float
    meta: dict
    node_cost_inr: float
    node_annual_inr: float
    sim: dict = field(default_factory=dict)
    xy: tuple = (0.0, 0.0)

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
    link_budget: dict = field(default_factory=dict)
    planning: dict = field(default_factory=dict)

    @property
    def monitored(self):
        return [a for a in self.assets.values() if a.monitored]

    @property
    def unmonitored(self):
        return [a for a in self.assets.values() if not a.monitored]

    def distance_m(self, asset, gw):
        return geo.haversine_m(asset.lat, asset.lon, gw.lat, gw.lon)

    def in_range(self, asset, gw, radius_scale=1.0, radius_m=None):
        r = (gw.radius_m if radius_m is None else radius_m) * radius_scale
        return self.distance_m(asset, gw) <= r + 1e-6

    def to_json(self):
        return {
            "district": self.district, "radio": self.radio, "costs": self.costs,
            "link_budget": self.link_budget, "planning": self.planning,
            "gateways": [{"id": g.id, "name": g.name, "lat": g.lat, "lon": g.lon, "xy_km": list(g.xy),
                          "existing": g.existing, "status": g.status, "capacity_nodes": g.capacity,
                          "max_nodes": g.capacity, "coverage_radius_km": g.radius_km, "assumed_radius_m": g.radius_m,
                          "cost_inr": g.cost_inr, "install_cost_inr": g.cost_inr, "annual_inr": g.annual_inr,
                          "coordinates_synthetic": True}
                         for g in self.gateways.values()],
            "assets": [{"id": a.id, "monitored": a.monitored, "gateway": a.gateway, "rating_kva": a.rating_kva,
                        "manufacturer": a.manufacturer, "cohort": a.cohort, "lat": a.lat, "lon": a.lon,
                        "xy_km": list(a.xy), "coordinates_synthetic": True, "metadata": a.meta,
                        "node_cost_inr": a.node_cost_inr, "node_annual_inr": a.node_annual_inr}
                       for a in self.assets.values()],
        }


def dist(a, b):
    """Planar distance between two local x/y km points (drawing helper only)."""
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


def _latlon(rec, where):
    if rec.get("lat") is None or rec.get("lon") is None:
        miss = " and ".join(k for k in ("lat", "lon") if rec.get(k) is None)
        raise FleetError(f"{where}: coordinates '{miss}' missing. Without a position the asset cannot be "
                         "placed on the map or checked against gateway range; add lat and lon in degrees.")
    try:
        lat, lon = float(rec["lat"]), float(rec["lon"])
    except (TypeError, ValueError):
        raise FleetError(f"{where}: 'lat' and 'lon' must be numbers, got {rec.get('lat')!r}, {rec.get('lon')!r}.") from None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise FleetError(f"{where}: 'lat'/'lon' out of range: {lat}, {lon}.")
    return lat, lon


def parse(cfg: dict) -> Fleet:
    if not isinstance(cfg, dict):
        raise FleetError("fleet file is empty or not a mapping")
    costs = cfg.get("costs") or {}
    radio = cfg.get("radio") or {}
    district = cfg.get("district") or {}
    for k in ("reporting_interval_min", "stale_after_missed", "offline_after_missed"):
        _num(radio, k, "radio", positive=True)
    origin = district.get("origin_latlon")
    gws = {}
    for g in cfg.get("gateways") or []:
        where = f"gateway {g.get('id', '?')}"
        if not g.get("id"):
            raise FleetError("a gateway record has no 'id'")
        if g["id"] in gws:
            raise FleetError(f"{where}: duplicate id")
        status = g.get("status")
        if status not in ("existing", "candidate"):
            raise FleetError(f"{where}: 'status' must be 'existing' or 'candidate', got {status!r}.")
        lat, lon = _latlon(g, where)
        gws[g["id"]] = Gateway(
            id=g["id"], name=g.get("name", g["id"]), lat=lat, lon=lon, existing=status == "existing",
            capacity=int(_num(g, "max_nodes", where, positive=True)),
            radius_m=_num(g, "assumed_radius_m", where, radio.get("assumed_radius_m"), positive=True),
            cost_inr=_num(g, "install_cost_inr", where, costs.get("gateway_cost_inr")),
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
        if mon and (gw not in gws or not gws[gw].existing):
            raise FleetError(f"{where}: monitored, so 'gateway' must name an existing gateway, got {gw!r}.")
        lat, lon = _latlon(a, where)
        assets[a["id"]] = Asset(
            id=a["id"], monitored=mon, gateway=gw if mon else None,
            rating_kva=_num(np_, "rating_kva", f"{where} nameplate", positive=True),
            manufacturer=str(a.get("manufacturer") or "unknown"), lat=lat, lon=lon,
            meta=dict(a.get("metadata") or {}),
            node_cost_inr=_num(a, "node_cost_inr", where, costs.get("node_cost_inr")),
            node_annual_inr=_num(a, "node_annual_inr", where, costs.get("node_annual_inr")),
            sim=dict(a.get("sim") or {}))
    if not assets:
        raise FleetError("no assets in the fleet file")
    if not origin:
        lats = [x.lat for x in list(assets.values()) + list(gws.values())]
        lons = [x.lon for x in list(assets.values()) + list(gws.values())]
        origin = (min(lats), min(lons))
    for x in list(assets.values()) + list(gws.values()):
        x.xy = tuple(round(v, 3) for v in geo.to_xy_km(x.lat, x.lon, origin))
    lb = dict(cfg.get("link_budget") or {})
    fl = Fleet(district, radio, costs, gws, assets, link_budget=lb, planning=dict(cfg.get("planning") or {}))
    for g in gws.values():
        used = sum(1 for a in fl.monitored if a.gateway == g.id)
        if used > g.capacity:
            raise FleetError(f"gateway {g.id}: {used} monitored nodes assigned, max_nodes is {g.capacity}.")
    for a in fl.monitored:
        if not fl.in_range(a, gws[a.gateway]):
            raise FleetError(f"asset {a.id}: {fl.distance_m(a, gws[a.gateway]) / 1000:.2f} km from its gateway "
                             f"{a.gateway}, beyond its {gws[a.gateway].radius_km:g} km assumed radius.")
    return fl


def load(path=FLEET_YAML) -> Fleet:
    with open(path) as f:
        return parse(yaml.safe_load(f))

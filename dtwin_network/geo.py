"""
Geometry and the radio estimate for the network twin. Five things are kept
apart for every transformer x gateway pair and never merged into one number:

  distance_m              geometry (haversine on the fleet file's lat/lon)
  within_assumed_radius   distance <= the gateway's assumed radius (an assumption)
  est_link_margin_db      a rough link-budget ESTIMATE (log-distance model), or None
                          when the link budget is switched off
  measured rssi / snr     only from simulated packets (dtwin_network/radio.py), labelled simulated
  comm_status             live, from the backend store

Feasible = within radius AND (link budget on -> margin >= required margin)
AND the gateway has capacity. The nearest gateway is never assumed feasible.
"""

import math

EARTH_R_M = 6_371_008.8          # mean Earth radius (IUGG)


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(math.sqrt(a))


def distance_matrix(assets, gateways):
    """{asset_id: {gateway_id: metres}} for every transformer and every gateway
    (existing and candidate)."""
    return {a.id: {g.id: haversine_m(a.lat, a.lon, g.lat, g.lon) for g in gateways} for a in assets}


def to_xy_km(lat, lon, origin):
    """Local east/north km from the district origin (equirectangular); used only
    to draw the offline SVG map, never for distances."""
    lat0, lon0 = origin
    return ((lon - lon0) * 111.32 * math.cos(math.radians(lat0)), (lat - lat0) * 111.32)


# ---------------------------------------------------------------- link budget
def fspl_db(d_km, f_mhz):
    return 20 * math.log10(max(d_km, 1e-6)) + 20 * math.log10(f_mhz) + 32.44


def path_loss_db(d_m, lb):
    """Log-distance: PL(d) = FSPL(d0) + 10 n log10(d / d0) + extra loss. A rough
    planning model, not a propagation prediction."""
    d0 = float(lb["reference_distance_m"])
    d = max(d_m, d0)
    return (fspl_db(d0 / 1000.0, lb["frequency_mhz"]) + 10 * lb["path_loss_exponent"] * math.log10(d / d0)
            + lb["extra_loss_db"])


def rx_power_dbm(d_m, lb):
    return (lb["tx_power_dbm"] + lb["tx_antenna_gain_dbi"] + lb["rx_antenna_gain_dbi"] - lb["cable_loss_db"]
            - path_loss_db(d_m, lb))


def link_margin_db(d_m, lb):
    """Estimated received power minus receiver sensitivity at the configured
    spreading factor."""
    sens = lb["sensitivity_dbm"][f"SF{int(lb['spreading_factor'])}"]
    return rx_power_dbm(d_m, lb) - sens


def noise_floor_dbm(lb):
    return -174 + 10 * math.log10(lb["bandwidth_hz"]) + lb["noise_figure_db"]


def pair(asset, gw, d_m, radius_m, lb=None, use_lb=False, room=True):
    """The separate fields for one transformer x gateway pair, and why it is or
    is not feasible."""
    within = d_m <= radius_m + 1e-6
    margin = round(link_margin_db(d_m, lb), 1) if lb else None
    radio_ok = (not use_lb) or (margin is not None and margin >= lb["required_margin_db"])
    feasible = within and radio_ok and room
    if not within:
        why = f"{d_m / 1000:.2f} km is beyond the assumed {radius_m / 1000:.1f} km radius"
    elif not radio_ok:
        why = f"estimated margin {margin:+.1f} dB below the required {lb['required_margin_db']:g} dB"
    elif not room:
        why = "gateway full"
    else:
        why = "within radius" + (f", estimated margin {margin:+.1f} dB" if use_lb else "") + ", has capacity"
    return {"gateway": gw.id, "distance_m": round(d_m), "within_assumed_radius": within,
            "est_link_margin_db": margin if use_lb else None, "radio_ok": radio_ok, "has_capacity": room,
            "feasible": feasible, "why": why}

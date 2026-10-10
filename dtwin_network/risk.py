"""
One transparent risk score per transformer, 0-100, with every term shown.

    risk = 100 x likelihood x (0.5 + 0.5 x consequence)

Likelihood, 0-1:
  monitored    the worst of what its own twin says now (worst evidence governs):
                 health term    (100 - health index) / 100
                 alarm term     1.0 for an active asset alarm, 0.3 when only an
                                instrument is at fault
                 limit term     0.9 hot-spot above its limit; 0.8 reaching it
                                within 2 h; 0.5 within 12 h
                 comms term     0.5 no data yet; 0.4 offline; 0.2 stale (its
                                condition is unknown, which is not "fine")
  unmonitored  metadata only, a weighted sum:
                 0.40 x load term      (peak-load ratio - 0.7) / 0.5, clipped 0-1
                 0.25 x age term       age / 35 years, clipped
                 0.35 x failure term   failures in the last 5 years / 3, clipped
Consequence, 0-1:
  0.6 x customers / 400 (clipped) + 0.4 x (criticality - 1) / 2   (criticality 1-3)

Bands: high >= 45, medium >= 25. Every constant is a stated judgement for a
synthetic fleet, not a fitted value.
"""

import numpy as np

HIGH, MEDIUM = 45.0, 25.0


def consequence(meta):
    cust = float(np.clip(float(meta.get("customers", 0)) / 400.0, 0, 1))
    crit = float(np.clip((float(meta.get("criticality", 1)) - 1) / 2.0, 0, 1))
    return {"value": round(0.6 * cust + 0.4 * crit, 3),
            "terms": [{"name": "customers served", "input": meta.get("customers"), "score": round(cust, 2), "weight": 0.6},
                      {"name": "criticality (1-3)", "input": meta.get("criticality"), "score": round(crit, 2), "weight": 0.4}]}


def band(r):
    return "high" if r >= HIGH else "medium" if r >= MEDIUM else "low"


def _finish(aid, monitored, like, terms, cons, driver, basis):
    r = round(100.0 * like * (0.5 + 0.5 * cons["value"]), 1)
    return {"id": aid, "monitored": monitored, "risk": r, "band": band(r), "likelihood": round(like, 3),
            "likelihood_terms": terms, "likelihood_rule": basis, "driver": driver, "consequence": cons}


def unmonitored(asset):
    m = asset.meta
    load = float(np.clip((float(m.get("peak_load_ratio", 0.7)) - 0.7) / 0.5, 0, 1))
    age = float(np.clip(float(m.get("age_years", 0)) / 35.0, 0, 1))
    fail = float(np.clip(float(m.get("past_failures_5y", 0)) / 3.0, 0, 1))
    terms = [{"name": "peak load", "input": f"{m.get('peak_load_ratio')} of rating", "score": round(load, 2), "weight": 0.40},
             {"name": "age", "input": f"{m.get('age_years')} years", "score": round(age, 2), "weight": 0.25},
             {"name": "failures, 5 y", "input": m.get("past_failures_5y"), "score": round(fail, 2), "weight": 0.35}]
    like = sum(t["score"] * t["weight"] for t in terms)
    driver = max(terms, key=lambda t: t["score"] * t["weight"])
    reason = {"peak load": f"peak load {m.get('peak_load_ratio')} of rating",
              "age": f"{m.get('age_years')} years old",
              "failures, 5 y": f"{m.get('past_failures_5y')} failures in 5 years"}[driver["name"]]
    return _finish(asset.id, False, like, terms, consequence(m), f"not monitored; {reason}", "weighted sum")


def monitored(asset, summary):
    h, c, tw = summary["health"], summary["comms"], summary.get("twin") or {}
    alerts = tw.get("alerts") or []
    asset_alarm = any(a["family"] != "sensor" and a["level"] == "alarm" for a in alerts)
    sensor_only = bool(alerts) and all(a["family"] == "sensor" for a in alerts)
    mins = tw.get("minutes_to_limit")
    limit = 0.9 if tw.get("above_limit") else 0.8 if mins is not None and mins <= 120 else \
        0.5 if mins is not None and mins <= 720 else 0.0
    comm = {"no data": 0.5, "offline": 0.4, "stale": 0.2}.get(c["status"], 0.0)
    terms = [{"name": "health", "input": h["index"], "score": round((100 - (h["index"] or 100)) / 100, 2)},
             {"name": "active alarm", "input": alerts[0]["what"] if alerts else "none",
              "score": 1.0 if asset_alarm else 0.3 if sensor_only else 0.0},
             {"name": "time to hot-spot limit", "input": "above now" if tw.get("above_limit") else
              (f"{mins:.0f} min" if mins is not None else "not reached"), "score": limit},
             {"name": "communication", "input": c["status"], "score": comm}]
    top = max(terms, key=lambda t: t["score"])
    like = top["score"]
    if top["score"] == 0:
        driver = "monitored; normal"
    elif top["name"] == "communication":
        driver = f"monitored but {c['status']}; condition unknown"
    elif top["name"] == "active alarm":
        driver = f"monitored; {alerts[0]['what'].lower()}"
    elif top["name"] == "time to hot-spot limit":
        driver = f"monitored; hot-spot {top['input'] if top['input'] == 'above now' else 'limit in ' + top['input']}"
    else:
        driver = f"monitored; health {h['index']:.0f} of 100"
    return _finish(asset.id, True, like, terms, consequence(asset.meta), driver, "worst term governs")


def all_risks(fleet, store):
    out = {}
    for a in fleet.assets.values():
        out[a.id] = monitored(a, store.summary(a.id)) if a.monitored else unmonitored(a)
    return out


def static_risks(fleet):
    """Unmonitored assets only (the planner's input): metadata, no live state."""
    return {a.id: unmonitored(a) for a in fleet.unmonitored}

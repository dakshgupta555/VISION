"""
What the Network twin page says, built from the store and the risk scores by
fixed templates. No language model anywhere: every sentence below is a format
string filled with numbers the twins or the planner produced.
"""

from . import risk as riskmod

LEVEL_RANK = {"alarm": 0, "warn": 1, "sensor": 2, "ok": 3, None: 4}


def kpis(store):
    sums = [store.summary(d) for d in store.devices]
    gws = store.gateways_json()
    alarms = sum(1 for s in sums if s["health"]["level"] in ("alarm", "warn") and s["health"]["verified"])
    sensor = sum(1 for s in sums if s["health"]["level"] == "sensor" and s["health"]["verified"])
    stale = sum(1 for s in sums if s["comms"]["status"] == "stale")
    off = sum(1 for s in sums if s["comms"]["status"] in ("offline", "no data"))
    used = sum(g["used"] for g in gws)
    cap = sum(g["capacity"] for g in gws)
    total = len(store.fleet.assets)
    return {"monitored": len(sums), "assets": total, "alarms": alarms, "sensor_faults": sensor,
            "stale": stale, "offline": off, "gateway_used": used, "gateway_capacity": cap,
            "gateway_pct": round(100.0 * used / cap) if cap else None}


def visit_first(store, risks, n=6):
    """Ranked by risk score; a unit the backend cannot see is ranked on its
    communication term, never as healthy."""
    rows = sorted(risks.values(), key=lambda r: (-r["risk"], r["id"]))
    out = []
    for r in rows[:n]:
        out.append({"id": r["id"], "risk": r["risk"], "band": r["band"], "monitored": r["monitored"],
                    "reason": r["driver"]})
    return out


def whats(store, risks):
    sums = {d: store.summary(d) for d in store.devices}
    k = kpis(store)
    ver = {d: s for d, s in sums.items() if s["health"]["verified"]}
    bad = sorted((s for s in ver.values() if s["health"]["level"] in ("alarm", "warn")),
                 key=lambda s: (LEVEL_RANK[s["health"]["level"]], -(risks[s["id"]]["risk"]), s["id"]))
    sens = sorted((s for s in ver.values() if s["health"]["level"] == "sensor"), key=lambda s: s["id"])
    dark = sorted((s for s in sums.values() if s["comms"]["status"] in ("stale", "offline", "no data")),
                  key=lambda s: s["id"])
    n_ok = sum(1 for s in ver.values() if s["health"]["level"] == "ok")
    acts, evidence = [], None
    if bad:
        top = bad[0]
        a = (top.get("twin") or {}).get("alerts") or []
        what = a[0]["what"] if a else "hot-spot over its limit"
        level = "alarm" if top["health"]["level"] == "alarm" else "warn"
        others = len(bad) - 1
        headline = f"{top['id']} needs attention: {what.lower()}" + (f" (and {others} more)" if others else "")
        meaning = (f"{len(bad)} transformer{'s' if len(bad) > 1 else ''} with an asset problem, "
                   f"{n_ok} normal and reporting"
                   + (f", {len(dark)} not reporting" if dark else "") + ". Each unit is judged on its own "
                   "measurements; the others are unaffected.")
        rec = ((top.get("twin") or {}).get("recommendations") or [{}])[0]
        acts.append({"priority": 1 if level == "alarm" else 2, "text": f"{top['id']}: {rec.get('text') or a[0]['action']}"})
        lp = top["comms"].get("last_packet") or {}
        evidence = (f"{top['id']} diagnosed by its own twin from packet #{lp.get('seq', '?')} "
                    f"({lp.get('ts', '')[11:16]}) via {top['comms'].get('last_gateway')}; "
                    + (a[0]["why"] if a else "hot-spot above its limit"))
    elif sens:
        top = sens[0]
        level = "sensor"
        a = top["twin"]["alerts"][0]
        headline = f"{top['id']}: an instrument is at fault, the transformer itself looks fine"
        meaning = f"{a['what']}. {n_ok} transformers normal" + (f", {len(dark)} not reporting." if dark else ".")
        acts.append({"priority": 2, "text": f"{top['id']}: {a['action']}"})
        evidence = a["why"]
    elif dark:
        level = "info"
        headline = (f"{len(dark)} transformer{'s are' if len(dark) > 1 else ' is'} not reporting; "
                    f"the {n_ok} that are reporting are normal")
        meaning = ("A unit that is not reporting is shown with its last known health, unverified, in grey. "
                   "Lost communication is not a transformer fault, and it is not 'healthy' either.")
        level = "info"
    else:
        level = "ok"
        headline = f"All {k['monitored']} monitored transformers normal, all reporting"
        meaning = (f"Every twin sees its unit where the model expects it. {k['assets'] - k['monitored']} "
                   "transformers in the district are not monitored; see the Deployment planner.")
    for s in dark[:1]:
        lp = s["comms"]
        acts.append({"priority": 2 if s["comms"]["status"] == "offline" else 3,
                     "text": f"Restore communication to {s['id']} ({s['comms']['status']}, last packet "
                             f"{(lp.get('last_seen') or '—')[11:16]} via {s['gateway']}). Its health is last known, "
                             "unverified."})
    if not acts and level == "ok":
        top_un = max((r for r in risks.values() if not r["monitored"]), key=lambda r: (r["risk"], r["id"]), default=None)
        if top_un:
            acts.append({"priority": 3, "text": f"Highest-risk unmonitored unit: {top_un['id']} "
                                                f"(risk {top_un['risk']:.0f}, {top_un['driver'].split('; ')[-1]})."})
    if evidence is None:
        lag = [s for s in ver.values()]
        evidence = (f"{len(ver)} of {k['monitored']} twins verified by a packet in the last "
                    f"{store.fleet.radio['stale_after_missed']} reporting intervals; "
                    f"{sum(s['comms'].get('received', 0) for s in sums.values())} packets received, "
                    f"{sum(s['comms'].get('lost', 0) for s in sums.values())} lost in the air.")
    return {"level": level, "headline": headline, "meaning": meaning, "actions": acts[:2], "evidence": evidence}


def overview(store):
    risks = riskmod.all_risks(store.fleet, store)
    devs = [store.summary(d) for d in store.devices]
    return {"clock": None if store.clock_sim is None else store.clock_sim.isoformat(),
            "receiving": store.last_uplink_wall is not None and store.wall() - store.last_uplink_wall < 12,
            "kpis": kpis(store), "whats": whats(store, risks), "visit_first": visit_first(store, risks),
            "devices": devs, "gateways": store.gateways_json(), "risks": risks,
            "events": list(store.events)[-60:][::-1], "demo": store.sim_config(),
            "disclaimer": store.fleet.district.get("disclaimer")}

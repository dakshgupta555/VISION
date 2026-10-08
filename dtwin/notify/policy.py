"""
Escalation policy: alert episodes -> incidents -> timed notification events.

Debounce and dedupe live here, and they matter more than the sending:

  incident   consecutive episodes of the same fault (same kind and title)
             merge into one incident unless the fault stays clear for
             clear_holdoff_h. A fault that flickers sends one notice, not ten.
  OPEN       one per incident per recipient, at onset ("immediate"), at the
             next working morning ("next_working_day"), or after the fault has
             lasted min_duration_min (debounces blips).
  ESCALATE   once, if the incident is still open escalate_after_h after onset.
  CLEAR      once, when the fault has stayed clear for clear_holdoff_h.
  DIGEST     one per day at digest.hour, regardless of alerts.

Every event is causal: the information it uses was available at its own time.
An incident's CLEAR is only emitted once its hold-off has elapsed with no
re-fire, so at any replay cursor the list of past events is exactly what a
live system would have sent by then.
"""

import json
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "escalation.json"

TIER_LABEL = {"P1": "P1 ALARM", "P2": "P2 ATTENTION", "P3": "P3 SENSOR", "DIGEST": "DAILY"}


def load_config(path=None):
    p = Path(path or DEFAULT_CONFIG)
    if p.suffix in (".yaml", ".yml"):
        import yaml                                    # optional; JSON needs nothing
        return yaml.safe_load(p.read_text())
    return json.loads(p.read_text())


def tier_of(alert):
    """P1 alarm on the asset, P2 attention on the asset, P3 instrument or model.
    Only P1 and P2 can ever mean sending a crew."""
    if alert["family"] == "asset":
        return "P1" if alert["level"] == "alarm" else "P2"
    return "P3"


@dataclass
class Incident:
    key: tuple
    tier: str
    kind: str
    what: str
    family: str
    start: pd.Timestamp
    end: pd.Timestamp                       # end of the last merged episode
    episodes: list = field(default_factory=list)
    open_at_end: bool = False               # still active at the end of the record


@dataclass
class Event:
    id: str
    time: pd.Timestamp
    type: str                               # OPEN, ESCALATE, CLEAR, DIGEST
    tier: str
    recipient: str
    channel: str
    attach_pdf: bool
    reason: str                             # why it fired, one line
    incident: Incident = None


def incidents(alerts, holdoff_h, record_end):
    hold = timedelta(hours=holdoff_h)
    by = {}
    for a in sorted(alerts, key=lambda a: a["start"]):
        by.setdefault((a["kind"], a["what"]), []).append(a)
    out = []
    for key, eps in by.items():
        cur = None
        for a in eps:
            if cur is not None and a["start"] - cur.end <= hold:
                cur.end = max(cur.end, a["end"])
                cur.episodes.append(a)
                continue
            cur = Incident(key, tier_of(a), a["kind"], a["what"], a["family"], a["start"], a["end"], [a])
            out.append(cur)
    for inc in out:
        inc.open_at_end = inc.end >= record_end
    return sorted(out, key=lambda i: i.start)


def _next_working(t, days, hour):
    c = t.normalize() + timedelta(hours=hour)
    if c < t or c.dayofweek not in days:
        c = (t.normalize() + timedelta(days=1)) + timedelta(hours=hour)
        while c.dayofweek not in days:
            c += timedelta(days=1)
    return c


def _active_at(inc, t, hold):
    """Is the incident still open at time t, as known at t? Open while an
    episode covers t, or while inside the hold-off after the last episode that
    had ended by t."""
    for a in inc.episodes:
        if a["start"] <= t <= a["end"]:
            return True
    ended = [a["end"] for a in inc.episodes if a["end"] < t]
    started = [a["start"] for a in inc.episodes if a["start"] <= t]
    return bool(started) and bool(ended) and t - max(ended) <= hold and max(ended) >= max(started)


def events(alerts, index, cfg, scenario, tag):
    """All notification events for a record, in time order."""
    hold_h = cfg["debounce"]["clear_holdoff_h"]
    hold = timedelta(hours=hold_h)
    days, hour = cfg.get("working_days", [0, 1, 2, 3, 4, 5]), cfg.get("working_hour", 9)
    record_end = index[-1]
    out = []

    def emit(time, typ, tier, targets, reason, inc=None, key=""):
        if time > record_end:
            return
        for tg in targets:
            eid = f"{scenario}|{key}|{typ}|{tg['to']}|{tg['channel']}"
            out.append(Event(eid, time, typ, tier, tg["to"], tg["channel"], bool(tg.get("attach_pdf")),
                             reason, inc))

    for inc in incidents(alerts, hold_h, record_end):
        rule = cfg["tiers"][inc.tier]
        key = f"{inc.kind}:{inc.what}:{inc.start:%Y%m%d%H%M}"
        min_dur = timedelta(minutes=rule.get("min_duration_min", 0))
        first_ep = inc.episodes[0]
        # debounce blips: the first episode must outlast min_duration, or the
        # incident must continue into a later episode
        if min_dur and (first_ep["end"] - first_ep["start"]) < min_dur and len(inc.episodes) == 1 \
                and not inc.open_at_end:
            continue
        t_open = inc.start + min_dur
        if rule.get("when") == "next_working_day":
            t_open = _next_working(t_open, days, hour)
        emit(t_open, "OPEN", inc.tier, rule["notify"], inc.what, inc, key)
        if rule.get("escalate_after_h") is not None:
            t_esc = inc.start + timedelta(hours=rule["escalate_after_h"])
            if t_esc > t_open and _active_at(inc, t_esc, hold):
                emit(t_esc, "ESCALATE", inc.tier, rule.get("escalate_to", []),
                     f"{inc.what} still active after {rule['escalate_after_h']} h", inc, key)
        if rule.get("notify_clear") and not inc.open_at_end:
            t_clr = max(inc.end + hold, t_open)
            emit(t_clr, "CLEAR", inc.tier, rule["notify"],
                 f"{inc.what} cleared at {inc.end:%d %b %H:%M}, no re-fire for {hold_h:g} h", inc, key)

    dg = cfg.get("digest")
    if dg:
        day = index[0].normalize() + timedelta(days=1, hours=dg["hour"])
        while day <= record_end:
            emit(day, "DIGEST", "DIGEST", dg["to"], f"daily digest {day:%d %b}", None, f"{day:%Y%m%d}")
            day += timedelta(days=1)

    out.sort(key=lambda e: (e.time, e.type != "DIGEST", e.id))
    return out

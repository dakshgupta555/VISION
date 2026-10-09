"""
Escalation and delivery checks. The replay guard is attacked, not assumed.

    python check_notify.py

  1  debounce    a fault flickering 10 times inside the hold-off sends one OPEN
                 and one CLEAR per recipient, not ten; showcase: at most one
                 OPEN / ESCALATE / CLEAR per incident per recipient
  2  replay      with SMTP credentials set, DTWIN_NOTIFY_LIVE=1 set, and every
                 network call rigged to explode, a full replay (and the API
                 endpoint) sends nothing: every message lands in the outbox
  3  layer 3     real notifiers refuse a replay message even if called directly
  4  live gate   live mode refuses to start without DTWIN_NOTIFY_LIVE=1
  5  historic    live mode on a historical file still sends nothing
  6  live path   with "now" at the event time, real channels are used, the
                 email carries the PDF, the SMS is <= 160 chars; with SMTP
                 unset the message falls back to the outbox and says so
  7  wording     P3 says "do not send a crew"; P1/P2 never do
  8  causality   nothing in the feed is later than the cursor; re-processing
                 the same cursor sends nothing new
"""

import os
import smtplib
import sys
import tempfile
import urllib.request
from datetime import timedelta
from pathlib import Path

import pandas as pd

from dtwin.api.engine import get_twin
from dtwin.notify import dispatch as D
from dtwin.notify.notifiers import GprsNotifier, Message, ReplayGuardError, SmtpNotifier
from dtwin.notify.policy import events, load_config

RESULTS = []
CALLS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


class Boom:
    """Stands in for smtplib.SMTP / urlopen: records the attempt and explodes."""
    def __init__(self, *a, **k):
        CALLS.append(("network", a))
        raise AssertionError("network call during replay")


def explode(*a, **k):
    CALLS.append(("urlopen", a))
    raise AssertionError("network call during replay")


SMTP_ENV = {"DTWIN_SMTP_HOST": "smtp.example.com", "DTWIN_SMTP_FROM": "twin@example.com",
            "DTWIN_SMTP_USER": "u", "DTWIN_SMTP_PASSWORD": "p", "DTWIN_SMS_GATEWAY_URL": "https://sms.example.com"}


def main():
    tw = get_twin("showcase")
    cfg = load_config()
    tmp = Path(tempfile.mkdtemp(prefix="outbox_"))

    # ---- 1 debounce ---------------------------------------------------------
    t0 = tw.rows.index[2000]
    flicker = [{"kind": "cooling", "what": "Cooling path degraded", "family": "asset", "level": "alarm",
                "start": t0 + timedelta(hours=2 * k), "end": t0 + timedelta(hours=2 * k, minutes=45),
                "why": "", "action": ""} for k in range(10)]
    ev = events(flicker, tw.rows.index, cfg, "flicker", "DT")
    ev = [e for e in ev if e.type != "DIGEST"]
    per = {}
    for e in ev:
        per[(e.type, e.recipient, e.channel)] = per.get((e.type, e.recipient, e.channel), 0) + 1
    ok = all(v == 1 for v in per.values()) and sum(1 for e in ev if e.type == "OPEN") == len(cfg["tiers"]["P1"]["notify"])
    check("debounce: 10 re-fires inside the hold-off", ok,
          f"{len(ev)} notices: " + ", ".join(f"{k[0]} {k[1]}/{k[2]} x{v}" for k, v in sorted(per.items())))

    real = [e for e in events(tw.alerts, tw.rows.index, cfg, "showcase", "DT") if e.type != "DIGEST"]
    cnt = {}
    for e in real:
        k = (e.incident.start, e.incident.what, e.type, e.recipient, e.channel)
        cnt[k] = cnt.get(k, 0) + 1
    check("debounce: showcase, one OPEN/ESCALATE/CLEAR per incident per recipient",
          max(cnt.values()) == 1, f"{len(tw.alerts)} episodes -> {len({(e.incident.start, e.incident.what) for e in real})} "
                                  f"incidents -> {len(real)} alert notices")

    # ---- 2 replay guard, attacked --------------------------------------------
    saved = (smtplib.SMTP, getattr(smtplib, "SMTP_SSL", None), urllib.request.urlopen, dict(os.environ))
    smtplib.SMTP = Boom
    smtplib.SMTP_SSL = Boom
    urllib.request.urlopen = explode
    os.environ.update(SMTP_ENV)
    os.environ["DTWIN_NOTIFY_LIVE"] = "1"
    try:
        d = D.Dispatcher(tw, mode="replay", outbox=tmp / "replay")
        sent = d.process(tw.rows.index[-1])
        D._PDF_POOL.submit(lambda: None).result()
        via = {m.delivered_via for m in sent}
        notes = all("suppressed" in m.note for m in sent)
        check("replay: full record, credentials set, live flag set, network rigged",
              not CALLS and via == {"outbox"} and notes,
              f"{len(sent)} messages, delivered via {via}, network attempts {len(CALLS)}")
        from dtwin.api import app as A
        A._DISPATCHERS.clear()
        r = A.notifications("showcase", t="2200")
        check("replay: the API endpoint", not CALLS and r["mode"] == "replay"
              and all(m["delivered_via"] == "outbox" for m in r["entries"]),
              f"{len(r['entries'])} entries, mode {r['mode']}, network attempts {len(CALLS)}")
    finally:
        smtplib.SMTP, smtplib.SMTP_SSL, urllib.request.urlopen = saved[0], saved[1], saved[2]
        os.environ.clear()
        os.environ.update(saved[3])

    # ---- 3 layer 3 -------------------------------------------------------------
    m = Message(id="x", time="", type="OPEN", tier="P1", recipient="r", recipient_name="r", address="a@b",
                channel="email", subject="s", body="b", sms="s", reason="r", replay=True)
    refused = []
    for n in (SmtpNotifier(SMTP_ENV), GprsNotifier(SMTP_ENV)):
        try:
            n.send(m)
        except ReplayGuardError:
            refused.append(n.name)
    check("layer 3: real notifiers refuse a replay message", refused == ["smtp", "gprs"], f"refused by {refused}")

    # ---- 4 live gate -----------------------------------------------------------
    try:
        D.Dispatcher(tw, mode="live", env={}, outbox=tmp / "x")
        check("live gate: refuses without DTWIN_NOTIFY_LIVE=1", False)
    except PermissionError:
        check("live gate: refuses without DTWIN_NOTIFY_LIVE=1", True)

    # ---- 5 historic file in live mode -------------------------------------------
    smtplib.SMTP = Boom
    urllib.request.urlopen = explode
    try:
        d = D.Dispatcher(tw, mode="live", env={**SMTP_ENV, "DTWIN_NOTIFY_LIVE": "1"}, outbox=tmp / "hist")
        d.events = [e for e in d.events if e.type != "DIGEST" and not e.attach_pdf][:12]
        sent = d.process(tw.rows.index[-1])
        check("historic: live mode on an old file sends nothing",
              not CALLS and all("historic" in m.note for m in sent), f"{len(sent)} messages, network attempts {len(CALLS)}")
    finally:
        smtplib.SMTP, urllib.request.urlopen = saved[0], saved[2]

    # ---- 6 live path, with fakes -------------------------------------------------
    got = {"smtp": [], "sms": []}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None): self.host = host
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, u, p): pass
        def send_message(self, em): got["smtp"].append(em)

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"ok"

    def fake_urlopen(req, timeout=None):
        got["sms"].append(req.data)
        return FakeResp()

    smtplib.SMTP, urllib.request.urlopen = FakeSMTP, fake_urlopen
    real_now = D.datetime
    try:
        d = D.Dispatcher(tw, mode="live", env={**SMTP_ENV, "DTWIN_NOTIFY_LIVE": "1"}, outbox=tmp / "live")
        target = next(e for e in d.events if e.tier == "P1" and e.type == "OPEN")
        d.events = [e for e in d.events if e.time == target.time and e.type == "OPEN"]

        class Now:
            @staticmethod
            def now():
                return target.time.to_pydatetime() + timedelta(minutes=1)
        D.datetime = Now
        sent = d.process(target.time)
        em = got["smtp"][0] if got["smtp"] else None
        has_pdf = bool(em) and any(p.get_content_type() == "application/pdf" for p in em.iter_attachments())
        sms_len = max((len(__import__("json").loads(b)["text"]) for b in got["sms"]), default=0)
        check("live path: email with PDF and SMS sent through real notifiers",
              {m.delivered_via for m in sent} == {"smtp", "gprs"} and has_pdf and 0 < sms_len <= 160,
              f"emails {len(got['smtp'])} (pdf attached: {has_pdf}), sms {len(got['sms'])} ({sms_len} chars)")
        d2 = D.Dispatcher(tw, mode="live", env={"DTWIN_NOTIFY_LIVE": "1"}, outbox=tmp / "live_nosmtp")
        d2.events = [e for e in d2.events if e.time == target.time and e.type == "OPEN"]
        sent2 = d2.process(target.time)
        check("live path: SMTP unset -> outbox, and says so",
              all(m.delivered_via == "outbox" and "not configured" in m.note for m in sent2),
              "; ".join(f"{m.channel}: {m.note}" for m in sent2))
    finally:
        smtplib.SMTP, urllib.request.urlopen = saved[0], saved[2]
        D.datetime = real_now

    # ---- 7 wording ---------------------------------------------------------------
    d = D.Dispatcher(tw, mode="replay", outbox=tmp / "replay")
    msgs = d.outbox.read_all()
    p3 = [m for m in msgs if m["tier"] == "P3" and m["type"] != "CLEAR"]
    p12 = [m for m in msgs if m["tier"] in ("P1", "P2")]
    check("wording: every P3 says do not send a crew; no P1/P2 does",
          p3 and all(D.NO_DISPATCH in m["body"] and "NODISPATCH" in m["sms"] for m in p3)
          and not any(D.NO_DISPATCH in m["body"] for m in p12), f"{len(p3)} P3, {len(p12)} P1/P2 messages")

    # ---- 8 causality -------------------------------------------------------------
    cur = tw.rows.index[1500]
    feed = d.feed(cur, limit=1000)
    again = D.Dispatcher(tw, mode="replay", outbox=tmp / "replay").process(cur)
    check("causality: feed never later than the cursor; re-processing sends nothing new",
          all(pd.Timestamp(m["time"]) <= cur for m in feed) and len(again) == 0,
          f"{len(feed)} entries up to {cur:%d %b %H:%M}, {len(again)} new on re-process")

    # ---- 9 demo button ---------------------------------------------------------
    from dtwin.notify import demo as DM
    off = DM.status(cfg, env={})
    check("demo: disabled without DTWIN_NOTIFY_LIVE=1, says why", not off["enabled"] and "DTWIN_NOTIFY_LIVE" in off["reason"],
          off["reason"])
    got["smtp"].clear()
    smtplib.SMTP = FakeSMTP
    try:
        env = {**SMTP_ENV, "DTWIN_NOTIFY_LIVE": "1"}
        d = D.Dispatcher(tw, mode="replay", outbox=tmp / "demo")
        cool = next(e for e in d.events if e.tier == "P1" and e.type == "OPEN" and "Cooling" in e.reason)
        d.process(cool.time)
        DM._SENT.clear()
        sent = DM.send_event(d, cool.id, env=env)
        to = sorted(em["To"] for em in got["smtp"])
        regular = {r.get("email") for r in cfg["recipients"].values()}
        demo_addr = {r["email"] for r in cfg["demo"]["recipients"].values()}
        pdf_ok = all(any(p.get_content_type() == "application/pdf" for p in em.iter_attachments()) for em in got["smtp"])
        tagged = all(em["Subject"].startswith("[DEMO - replay of") for em in got["smtp"])
        check("demo: one click sends the whole P1 event to the demo addresses only",
              len(sent) == 2 and set(to) <= demo_addr and not set(to) & regular and pdf_ok and tagged,
              f"to {to}, PDF attached {pdf_ok}, [DEMO] tagged {tagged}")
        DM._SENT.extend([{}] * (cfg["demo"]["max_sends"] - len(DM._SENT)))
        try:
            DM.send_event(d, cool.id, env=env)
            check("demo: cap of max_sends per session", False)
        except PermissionError as e:
            check("demo: cap of max_sends per session", True, str(e))
        DM._SENT.clear()
    finally:
        smtplib.SMTP = saved[0]

    D._PDF_POOL.submit(lambda: None).result()
    n_ok = sum(ok for _, ok, _ in RESULTS)
    print(f"\n  {n_ok} / {len(RESULTS)} checks pass   (outboxes in {tmp})")
    return 0 if n_ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

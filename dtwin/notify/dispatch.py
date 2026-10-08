"""
Turn policy events into messages and route them.

Replay guard, layers 1 and 2 of 3:
  1. A Dispatcher is "replay" unless constructed with mode="live", and live
     mode refuses to start unless DTWIN_NOTIFY_LIVE=1 is set in the
     environment. The API only ever constructs replay dispatchers; nothing in a
     request can change that.
  2. In replay mode every message goes to the outbox, whatever channel the
     policy asked for; the message records which real channel was suppressed.
     In live mode, events older than live_window_min are also outboxed only,
     so pointing live mode at a historical file still cannot spam anyone.
(Layer 3 is in notifiers.py: real notifiers refuse replay messages.)
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from .notifiers import GprsNotifier, Message, OutboxNotifier, SmtpNotifier
from .policy import ROOT, TIER_LABEL, events, load_config

_PDF_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdf")
NO_DISPATCH = "INSTRUMENT FAULT - do not send a crew for a transformer fault."


class Dispatcher:
    def __init__(self, tw, mode="replay", config=None, outbox=None, env=None, live_window_min=30):
        if mode not in ("replay", "live"):
            raise ValueError(mode)
        env = os.environ if env is None else env
        if mode == "live" and env.get("DTWIN_NOTIFY_LIVE") != "1":
            raise PermissionError("live notifications need DTWIN_NOTIFY_LIVE=1 in the environment")
        self.tw, self.mode, self.env = tw, mode, env
        self.cfg = config or load_config()
        tag = f"{tw.name}" + (f"_{tw.np_.rating_kva:g}kva" if tw.report and tw.report.nameplate.get("how") == "override" else "")
        self.outbox = OutboxNotifier(Path(outbox or ROOT / self.cfg.get("outbox", "outbox")) / tag)
        self.smtp, self.gprs = SmtpNotifier(env), GprsNotifier(env)
        self.live_window = timedelta(minutes=live_window_min)
        self.events = events(tw.alerts, tw.rows.index, self.cfg, tw.name, tw.np_.tag)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ status
    def status(self):
        return {"mode": self.mode, "outbox": str(self.outbox.folder),
                "smtp_configured": self.smtp.configured(), "sms_configured": self.gprs.configured(),
                "guard": ("replay: every message goes to the outbox; real email and SMS are disabled"
                          if self.mode == "replay" else
                          f"live: real delivery for events within {self.live_window} of now")}

    # ---------------------------------------------------------------- process
    def process(self, upto):
        """Deliver every event at or before `upto` that has not been delivered.
        Idempotent: the outbox is the record of what has been sent."""
        upto = pd.Timestamp(upto)
        sent = []
        with self._lock:
            for ev in self.events:
                if ev.time > upto:
                    break
                if self.outbox.exists(ev.id):
                    continue
                sent.append(self._deliver(self._compose(ev)))
        return sent

    def feed(self, upto, limit=40):
        upto = pd.Timestamp(upto)
        rows = [m for m in self.outbox.read_all() if pd.Timestamp(m["time"]) <= upto]
        rows.sort(key=lambda m: (m["time"], m["id"]), reverse=True)
        return rows[:limit]

    # ---------------------------------------------------------------- compose
    def _compose(self, ev):
        tw, np_ = self.tw, self.tw.np_
        st = tw.state(ev.time)
        rcp = self.cfg["recipients"].get(ev.recipient, {"name": ev.recipient})
        address = rcp.get("sms" if ev.channel == "sms" else "email", "")
        r, cd = st["readings"], st["countdown"]
        lines, headline = [], ev.reason
        inc = ev.incident
        alert = None
        if inc is not None:
            alert = next((a for a in st["alerts_live"] if a["what"] == inc.what), None)
            src = alert or inc.episodes[-1]
            headline = alert["headline"] if alert else inc.what

        if ev.type == "DIGEST":
            subject = f"[{TIER_LABEL['DIGEST']}] {np_.tag} digest {ev.time:%d %b %Y}"
            day = [a for a in tw.alerts if ev.time - timedelta(hours=24) <= a["start"] <= ev.time]
            lines += [f"Status {st['status']['level'].upper()}, health index {st['health']['index']:.0f}/100, "
                      f"{len(st['alerts_live'])} active alert(s).",
                      *[f"  active: {a['headline']}" for a in st["alerts_live"]],
                      f"Last 24 h: {len(day)} new episode(s)"
                      + (": " + "; ".join(sorted({a['what'] for a in day})) if day else "."),
                      f"Insulation life last 24 h: {st['life']['last_24h']['aged_h']} aged h "
                      f"(Rs {st['life']['last_24h']['inr']:.0f}); whole record "
                      f"{st['life']['since_start']['aged_h']} h (Rs {st['life']['since_start']['inr']:.0f}).",
                      "Full condition report attached."]
        else:
            label = TIER_LABEL[ev.tier] + {"OPEN": "", "ESCALATE": " ESCALATION", "CLEAR": " CLEARED"}[ev.type]
            subject = f"[{label}] {np_.tag} {inc.what}"
            if ev.tier == "P3" and ev.type != "CLEAR":
                lines.append(NO_DISPATCH)
            if ev.type == "CLEAR":
                dur = sum((a["end"] - a["start"]).total_seconds() for a in inc.episodes) / 3600
                lines += [f"Resolved: {inc.what}.",
                          f"Active {dur:.1f} h across {len(inc.episodes)} episode(s), "
                          f"{inc.start:%d %b %H:%M} to {inc.end:%d %b %H:%M}; no re-fire since.",
                          "No action needed unless it recurs."]
            else:
                if ev.type == "ESCALATE":
                    lines.append(f"Still active {(ev.time - inc.start).total_seconds() / 3600:.0f} h after "
                                 f"onset at {inc.start:%d %b %H:%M}. Escalated.")
                elif alert is None:
                    lines.append(f"Started {inc.start:%d %b %H:%M}; not active at the time of this notice.")
                lines += [headline, f"Why: {src['why']}", f"Do: {src['action']}"]
                if ev.tier == "P3":
                    lines.append("The transformer diagnosis continues on the remaining channels.")
                else:
                    h = cd["hotspot"]
                    lines.append(f"Now: oil {r['oil_c']} C, hot-spot {r['hotspot_c']} C, load {r['load_pct']:.0f}%, "
                                 f"ambient {r['ambient_c']} C. "
                                 + ("Hot-spot limit exceeded." if h["already_above"] else
                                    f"Hot-spot {h['limit_c']:.0f} C in {h['minutes']:.0f} min." if h["minutes"] is not None
                                    else "Hot-spot limit not reached at present load.")
                                 + (f" Shed {h['shed_pct']:.0f}% load." if h["shed_pct"] else ""))
        if ev.attach_pdf:
            lines.append("Condition report (PDF) attached.")
        lines.append(f"-- {np_.tag} digital twin, event time {ev.time:%d %b %Y %H:%M}"
                     + (" (replay)" if self.mode == "replay" else ""))

        frame = st["gprs"]["message"] + f",{ev.type[:3]}" + (",NODISPATCH" if ev.tier == "P3" else "")
        return Message(id=ev.id, time=ev.time.isoformat(), type=ev.type, tier=ev.tier,
                       recipient=ev.recipient, recipient_name=rcp.get("name", ev.recipient),
                       address=address, channel=ev.channel, subject=subject,
                       body="\n".join(lines), sms=frame[:160], reason=ev.reason,
                       replay=self.mode == "replay", attach_pdf=ev.attach_pdf,
                       meta={"incident_start": inc.start.isoformat() if inc else None,
                             "family": inc.family if inc else None})

    # ---------------------------------------------------------------- deliver
    def _deliver(self, msg):
        if msg.attach_pdf:
            pdf = self.outbox.folder / "pdf" / f"{pd.Timestamp(msg.time):%Y%m%d_%H%M}.pdf"
            msg.attachment = str(pdf)
            msg.meta["attachment_status"] = "ready" if pdf.exists() else "rendering"
        real = {"email": self.smtp, "sms": self.gprs}.get(msg.channel)

        if self.mode == "replay":
            msg.delivered_via = "outbox"
            msg.note = f"replay - {msg.channel} to {msg.address or msg.recipient_name} suppressed"
            self.outbox.send(msg)
            if msg.attach_pdf and msg.meta["attachment_status"] != "ready":
                _PDF_POOL.submit(self._render, msg)
            return msg

        # live: render first so the email carries the PDF
        if msg.attach_pdf and msg.meta["attachment_status"] != "ready":
            self._render(msg, update=False)
        age = pd.Timestamp(datetime.now()) - pd.Timestamp(msg.time)
        if age > self.live_window:
            msg.delivered_via, msg.note = "outbox", f"historic event ({age} old) - not sent"
        elif real is None or not real.configured():
            msg.delivered_via = "outbox"
            msg.note = f"{real.name if real else msg.channel} not configured - kept in outbox"
        else:
            try:
                real.send(msg)
            except Exception as e:                       # never lose the message
                msg.delivered_via, msg.note = "outbox", f"{real.name} failed: {e}"
        self.outbox.send(msg)
        return msg

    def _render(self, msg, update=True):
        from ..report import build_pdf
        pdf = Path(msg.attachment)
        try:
            if not pdf.exists():
                pdf.parent.mkdir(parents=True, exist_ok=True)
                tmp = pdf.with_suffix(".part")
                tmp.write_bytes(build_pdf(self.tw, pd.Timestamp(msg.time)))
                tmp.replace(pdf)
            msg.meta["attachment_status"] = "ready"
        except Exception as e:
            msg.meta["attachment_status"] = f"failed: {e}"
        if update:
            self.outbox.update(msg)

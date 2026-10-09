"""
"Send for real (demo)": one explicit click on the dashboard turns one event
from the replay into real emails, so a judge can watch an alarm arrive.

This is the only path by which anything from a replay leaves the machine, and
it is deliberately narrow:

  - a person clicks it; scrubbing, Play and the automatic dispatcher never call it
  - it needs DTWIN_NOTIFY_LIVE=1 and SMTP configured, else it explains why not
  - it sends only to config["demo"]["recipients"], rerouted by role; the
    regular recipient addresses are never used
  - every subject says [DEMO - replay of <record time>]
  - at most config["demo"]["max_sends"] emails per backend session

One click sends every recipient row of the clicked event: a P1 alarm reaches
the site engineer and the maintenance head together. SMS rows arrive as email
too, carrying the 160-character frame. Every demo email carries the full
picture at the event time and the PDF condition report.
"""

import html
import os
import threading
from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path

import pandas as pd

from .notifiers import SmtpNotifier
from .policy import TIER_LABEL, incidents

TONE = {"P1": "#dc2626", "P2": "#d97706", "P3": "#0891b2", "DIGEST": "#475569"}
_LOCK = threading.Lock()
_SENT = []                                     # this backend session's demo emails


def status(cfg, env=None):
    env = os.environ if env is None else env
    demo = cfg.get("demo") or {}
    smtp = SmtpNotifier(env)
    left = max(0, int(demo.get("max_sends", 10)) - len(_SENT))
    reason = None
    if not demo.get("recipients"):
        reason = "no demo recipients in config/escalation.json"
    elif env.get("DTWIN_NOTIFY_LIVE") != "1":
        reason = "set DTWIN_NOTIFY_LIVE=1 in .env and restart the backend"
    elif not smtp.configured():
        reason = "SMTP not configured: fill DTWIN_SMTP_* in .env and restart the backend"
    elif left == 0:
        reason = f"demo limit of {demo.get('max_sends', 10)} emails reached; restart the backend to reset"
    return {"enabled": reason is None, "reason": reason, "sends_left": left,
            "recipients": sorted({r["email"] for r in demo.get("recipients", {}).values()}),
            "sent": list(_SENT)}


def send_event(dispatcher, msg_id, env=None):
    """Send every recipient row of the event `msg_id` belongs to."""
    cfg = dispatcher.cfg
    st = status(cfg, env)
    if not st["enabled"]:
        raise PermissionError(st["reason"])
    rows = dispatcher.outbox.read_all()
    msg = next((m for m in rows if m["id"] == msg_id), None)
    if msg is None:
        raise KeyError(f"no such message {msg_id}")
    siblings = [m for m in rows if m["time"] == msg["time"] and m["type"] == msg["type"]
                and m["reason"] == msg["reason"]]
    demo = cfg["demo"]["recipients"]
    smtp = SmtpNotifier(env)
    out = []
    with _LOCK:
        budget = int(cfg["demo"].get("max_sends", 10)) - len(_SENT)
        if len(siblings) > budget:
            raise PermissionError(f"this event needs {len(siblings)} emails; {budget} demo sends left")
        pdf = _ensure_pdf(dispatcher, msg)
        for m in siblings:
            to = demo.get(m["recipient"]) or next(iter(demo.values()))
            em = _compose(dispatcher, m, to, pdf)
            smtp.deliver(em)
            rec = {"id": m["id"], "to": to["email"], "as": to["name"], "subject": em["Subject"],
                   "event_time": m["time"], "sent_at": pd.Timestamp.now().isoformat(timespec="seconds")}
            _SENT.append(rec)
            out.append(rec)
            m.setdefault("meta", {})["demo_sent"] = rec
            dispatcher.outbox.path_for(m["id"], "json").write_text(__import__("json").dumps(m, indent=2))
    return out


def _ensure_pdf(dispatcher, msg):
    from ..report import build_pdf
    pdf = dispatcher.outbox.folder / "pdf" / f"{pd.Timestamp(msg['time']):%Y%m%d_%H%M}.pdf"
    if not pdf.exists():
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(build_pdf(dispatcher.tw, pd.Timestamp(msg["time"])))
    return pdf


def _compose(dispatcher, m, to, pdf):
    tw = dispatcher.tw
    np_ = tw.np_
    t = pd.Timestamp(m["time"])
    st = tw.state(t)
    r, cd, hl = st["readings"], st["countdown"], st["health"]
    tier = m["tier"]
    role = dispatcher.cfg["recipients"].get(m["recipient"], {}).get("name", m["recipient"])
    subject = f"[DEMO - replay of {t:%d %b %H:%M}] {m['subject']}"

    # every incident up to the event time, with its status then
    hold = dispatcher.cfg["debounce"]["clear_holdoff_h"]
    past = [a for a in tw.alerts if a["start"] <= t]
    incs = incidents(past, hold, t)
    summary = []
    for inc in incs:
        active = any(a["start"] <= t <= a["end"] for a in inc.episodes)
        summary.append((inc.start, inc.tier, inc.what, "ACTIVE" if active else f"cleared {inc.end:%d %b %H:%M}"))

    h = cd["hotspot"]
    proj = ("hot-spot limit exceeded now" if h["already_above"] else
            f"hot-spot {h['limit_c']:.0f} C in {h['minutes']:.0f} min" if h["minutes"] is not None else
            f"hot-spot stays under {h['limit_c']:.0f} C at present load")
    proj += f", settles at {h['settles_at_c']} C" + (f"; shed {h['shed_pct']:.0f}% load" if h["shed_pct"] else "")
    fixed = cd["fixed"]
    dev = ("already exceeded" if fixed["already_above"] else
           f"in {fixed['minutes']:.0f} min" if fixed["minutes"] is not None else "not reached")

    # ---- plain text --------------------------------------------------------
    T = [f"DEMO - this is a replay of record time {t:%d %b %Y %H:%M}, sent on request from the dashboard.",
         f"In live operation it goes to: {role} via {m['channel']}.", "",
         m["subject"], "", m["body"], "",
         "Readings at that moment:",
         f"  oil {r['oil_c']} C (twin {r['oil_pred_c']} C), hot-spot {r['hotspot_c']} C, ambient {r['ambient_c']} C",
         f"  load {r['load_pct']:.0f}% (worst phase {r['load_worst_phase_pct']:.0f}%), current unbalance {r['current_unbalance_pct']}%",
         f"  rise-fit slope {r['slope']}, intercept {r['intercept_k']} K, ageing factor {r['faa']}",
         f"Projection: {proj}.",
         f"Existing fixed {fixed['limit_c']:.0f} C oil trip: {dev} (comparison only).",
         f"Health index {hl['index']:.0f}/100: " + ", ".join(
             f"{c['label']} {c['score']:.0f}" for c in hl["components"] if c["score"] is not None),
         "", "Fault summary to date:"]
    T += [f"  {s:%d %b %H:%M}  {TIER_LABEL.get(ti, ti):<13} {w}  [{state}]" for s, ti, w, state in summary] or ["  none"]
    T += ["", f"SMS frame ({len(m['sms'])} chars): {m['sms']}", "",
          f"Condition report attached: {pdf.name}",
          f"-- {np_.tag}, {np_.rating_kva:.0f} kVA distribution transformer digital twin"]

    # ---- html ---------------------------------------------------------------
    e = html.escape
    tone = TONE.get(tier, "#475569")
    body_lines = "".join(f"<p style='margin:4px 0'>{e(x)}</p>" for x in m["body"].split("\n") if x.strip())
    comp_rows = "".join(_comp_row(c) for c in hl["components"])
    sum_rows = "".join(
        f"<tr><td style='white-space:nowrap'>{s:%d %b %H:%M}</td><td style='color:{TONE.get(ti, '#475569')}'>{e(ti)}</td>"
        f"<td>{e(w)}</td><td style='color:{'#dc2626' if state == 'ACTIVE' else '#16a34a'}'>{e(state)}</td></tr>"
        for s, ti, w, state in summary) or "<tr><td colspan=4>none</td></tr>"
    H = f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;color:#0b0f14">
<div style="background:#fef3c7;border:1px solid #d97706;padding:6px 10px;font-size:12px">
<b>DEMO</b> - replay of record time {t:%d %b %Y %H:%M}, sent on request from the dashboard.
In live operation this goes to <b>{e(role)}</b> via {e(m['channel'])}.</div>
<h2 style="color:{tone};margin:12px 0 4px">{e(m['subject'])}</h2>
{body_lines}
<h3 style="margin:14px 0 4px">Readings at that moment</h3>
<table style="font-size:13px;border-collapse:collapse" cellpadding="3">
<tr><td>Top oil</td><td><b>{r['oil_c']} C</b> (twin predicts {r['oil_pred_c']} C)</td></tr>
<tr><td>Hot-spot</td><td><b>{r['hotspot_c']} C</b></td></tr>
<tr><td>Ambient</td><td>{r['ambient_c']} C</td></tr>
<tr><td>Load</td><td>{r['load_pct']:.0f}% (worst phase {r['load_worst_phase_pct']:.0f}%)</td></tr>
<tr><td>Current unbalance</td><td>{r['current_unbalance_pct']}%</td></tr>
<tr><td>Rise-fit slope / intercept</td><td>{r['slope']} / {r['intercept_k']} K</td></tr>
<tr><td>Projection</td><td>{e(proj)}</td></tr>
<tr><td>Existing {fixed['limit_c']:.0f} C trip</td><td>{e(dev)} <span style="color:#64748b">(comparison only)</span></td></tr>
</table>
<h3 style="margin:14px 0 4px">Health index {hl['index']:.0f}/100</h3>
<table style="font-size:13px;border-collapse:collapse" cellpadding="3">{comp_rows}</table>
<h3 style="margin:14px 0 4px">Fault summary to date</h3>
<table style="font-size:13px;border-collapse:collapse" cellpadding="3">{sum_rows}</table>
<p style="font-size:12px;color:#64748b;margin-top:14px">SMS frame ({len(m['sms'])} chars):
<code>{e(m['sms'])}</code><br>Full condition report attached ({e(pdf.name)}).<br>
{e(np_.tag)}, {np_.rating_kva:.0f} kVA distribution transformer digital twin.</p></div>"""

    em = EmailMessage()
    em["To"] = to["email"]
    em["Subject"] = subject
    em.set_content("\n".join(T))
    em.add_alternative(H, subtype="html")
    em.add_attachment(Path(pdf).read_bytes(), maintype="application", subtype="pdf",
                      filename=f"{np_.tag}_{t:%Y%m%d_%H%M}_condition_report.pdf")
    return em


def _comp_row(c):
    e = html.escape
    value = "" if c["value"] is None else f"{c['value']} {c['unit']}"
    score = "-" if c["score"] is None else f"{c['score']:.0f}"
    return (f"<tr><td>{e(c['label'])}</td><td style='text-align:right'>{e(value)}</td>"
            f"<td style='text-align:right;font-weight:bold;color:{_band(c['score'])}'>{score}</td></tr>")


def _band(sc):
    if sc is None:
        return "#64748b"
    return "#16a34a" if sc > 80 else "#d97706" if sc >= 40 else "#dc2626"

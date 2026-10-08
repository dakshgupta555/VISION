"""
    python -m dtwin.notify replay showcase [--reset]     # fill the outbox for the whole record
    python -m dtwin.notify plan showcase                 # list what would be sent, write nothing
    python -m dtwin.notify test-email you@example.com    # one real test email (needs SMTP env vars)

Replay never sends anything for real. There is deliberately no command that
replays history through real channels.
"""

import argparse
import shutil
import sys

from ..api.engine import get_twin
from .dispatch import Dispatcher
from .notifiers import Message, SmtpNotifier


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("replay", "plan"):
        p = sub.add_parser(c)
        p.add_argument("scenario")
        p.add_argument("--reset", action="store_true")
    t = sub.add_parser("test-email")
    t.add_argument("to")
    a = ap.parse_args(argv)

    if a.cmd == "test-email":
        s = SmtpNotifier()
        if not s.configured():
            print("SMTP not configured: set DTWIN_SMTP_HOST and DTWIN_SMTP_FROM (and USER/PASSWORD).")
            return 1
        m = Message(id="test", time="", type="TEST", tier="-", recipient="test", recipient_name=a.to,
                    address=a.to, channel="email", subject="Digital twin: test message",
                    body="SMTP configuration works.", sms="", reason="test", replay=False)
        s.send(m)
        print(f"sent to {a.to}")
        return 0

    tw = get_twin(a.scenario)
    d = Dispatcher(tw, mode="replay")
    if a.cmd == "plan":
        for ev in d.events:
            print(f"{ev.time:%d %b %H:%M}  {ev.type:<8} {ev.tier:<6} {ev.recipient:<17} {ev.channel:<5} "
                  f"{'pdf ' if ev.attach_pdf else '    '}{ev.reason}")
        print(f"\n{len(d.events)} notifications from {len(tw.alerts)} alert episodes")
        return 0
    if a.reset and d.outbox.folder.exists():
        shutil.rmtree(d.outbox.folder)
        d = Dispatcher(tw, mode="replay")
    sent = d.process(tw.rows.index[-1])
    print(f"{len(sent)} new messages in {d.outbox.folder} (PDFs render in the background)")
    from .dispatch import _PDF_POOL
    _PDF_POOL.shutdown(wait=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Notifications for the live stream: the same policy and debounce as replay,
delivered to the OUTBOX only. An injected fault therefore shows the
notification it would trigger; a real send happens only through the
dashboard's per-row "Send for real" button.

Event ids depend only on when each incident started, so running this after
every sample is idempotent: each notification is written once.
"""

import shutil
import threading
from pathlib import Path

from ..notify.dispatch import Dispatcher
from ..notify.policy import ROOT, load_config

_LOCK = threading.Lock()


def outbox_dir(cfg=None):
    cfg = cfg or load_config()
    return Path(ROOT / cfg.get("outbox", "outbox")) / "live"


def dispatcher(tw, cfg=None):
    """A fresh outbox-only dispatcher over the live twin as it stands now."""
    return Dispatcher(tw, mode="outbox", config=cfg)


def notify(tw, cfg=None):
    """Deliver every event up to the newest sample that is not yet in the
    outbox. Returns the new messages."""
    if tw is None:
        return []
    with _LOCK:
        d = dispatcher(tw, cfg)
        return d.process(tw.rows.index[-1])


def clear(cfg=None):
    """A new live stream starts with an empty live outbox."""
    shutil.rmtree(outbox_dir(cfg), ignore_errors=True)

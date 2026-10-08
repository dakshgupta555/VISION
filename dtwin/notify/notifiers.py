"""
Delivery channels behind one interface.

  OutboxNotifier   writes the message (and its PDF) to an outbox folder. The
                   demo default: always works, nothing to configure.
  SmtpNotifier     real email. Credentials only from environment variables:
                     DTWIN_SMTP_HOST, DTWIN_SMTP_PORT (587), DTWIN_SMTP_USER,
                     DTWIN_SMTP_PASSWORD, DTWIN_SMTP_FROM, DTWIN_SMTP_TLS (1)
  GprsNotifier     the 160-character payload, posted to an SMS gateway given by
                     DTWIN_SMS_GATEWAY_URL (optional DTWIN_SMS_GATEWAY_TOKEN)

Replay guard, layer 3 of 3: every real notifier refuses any message marked
replay, whatever called it. The dispatcher (layers 1 and 2) should never pass
one, but a replay must not be able to reach a real recipient even by mistake.
"""

import json
import os
import smtplib
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from email.message import EmailMessage
from pathlib import Path


class ReplayGuardError(RuntimeError):
    """Raised when anything tries to send a replay message for real."""


@dataclass
class Message:
    id: str
    time: str                       # ISO, the event time in the record
    type: str
    tier: str
    recipient: str                  # key from the config
    recipient_name: str
    address: str                    # email or phone number, may be empty
    channel: str                    # what the policy asked for: email, sms
    subject: str
    body: str
    sms: str                        # the 160-char frame
    reason: str
    replay: bool = True
    attach_pdf: bool = False
    attachment: str = ""            # path once rendered
    delivered_via: str = ""
    note: str = ""
    meta: dict = field(default_factory=dict)


class Notifier(ABC):
    name = "abstract"
    real = False                    # True for anything that leaves this machine

    def send(self, msg: Message) -> Message:
        if self.real and msg.replay:
            raise ReplayGuardError(f"{self.name} refused replay message {msg.id}")
        return self._send(msg)

    @abstractmethod
    def _send(self, msg: Message) -> Message:
        ...

    def configured(self) -> bool:
        return True


class OutboxNotifier(Notifier):
    name = "outbox"

    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def path_for(self, msg_id, ext):
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in msg_id)
        return self.folder / f"{safe}.{ext}"

    def exists(self, msg_id):
        return self.path_for(msg_id, "json").exists()

    def _send(self, msg):
        if not msg.delivered_via:
            msg.delivered_via = "outbox"
        txt = (f"To: {msg.recipient_name} <{msg.address}> via {msg.channel}\n"
               f"Delivered: {msg.delivered_via}{' - ' + msg.note if msg.note else ''}\n"
               f"Time: {msg.time}\nSubject: {msg.subject}\n\n{msg.body}\n\nSMS frame: {msg.sms}\n")
        self.path_for(msg.id, "txt").write_text(txt)
        self.path_for(msg.id, "json").write_text(json.dumps(asdict(msg), indent=2, default=str))
        return msg

    def update(self, msg):
        self.path_for(msg.id, "json").write_text(json.dumps(asdict(msg), indent=2, default=str))

    def read_all(self):
        out = []
        for p in sorted(self.folder.glob("*.json")):
            try:
                out.append(json.loads(p.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
        return out


class SmtpNotifier(Notifier):
    name = "smtp"
    real = True

    def __init__(self, env=None):
        e = os.environ if env is None else env
        self.host = e.get("DTWIN_SMTP_HOST", "")
        self.port = int(e.get("DTWIN_SMTP_PORT", "587") or 587)
        self.user = e.get("DTWIN_SMTP_USER", "")
        self.password = e.get("DTWIN_SMTP_PASSWORD", "")
        self.sender = e.get("DTWIN_SMTP_FROM", self.user)
        self.tls = e.get("DTWIN_SMTP_TLS", "1") != "0"

    def configured(self):
        return bool(self.host and self.sender)

    def _send(self, msg):
        em = EmailMessage()
        em["From"], em["To"], em["Subject"] = self.sender, msg.address, msg.subject
        em.set_content(msg.body)
        if msg.attachment and Path(msg.attachment).exists():
            em.add_attachment(Path(msg.attachment).read_bytes(), maintype="application",
                              subtype="pdf", filename=Path(msg.attachment).name)
        with smtplib.SMTP(self.host, self.port, timeout=20) as s:
            if self.tls:
                s.starttls()
            if self.user:
                s.login(self.user, self.password)
            s.send_message(em)
        msg.delivered_via = "smtp"
        return msg


class GprsNotifier(Notifier):
    name = "gprs"
    real = True

    def __init__(self, env=None):
        e = os.environ if env is None else env
        self.url = e.get("DTWIN_SMS_GATEWAY_URL", "")
        self.token = e.get("DTWIN_SMS_GATEWAY_TOKEN", "")

    def configured(self):
        return bool(self.url)

    def _send(self, msg):
        body = json.dumps({"to": msg.address, "text": msg.sms[:160]}).encode()
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {self.token}"} if self.token else {})})
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
        msg.delivered_via = "gprs"
        return msg

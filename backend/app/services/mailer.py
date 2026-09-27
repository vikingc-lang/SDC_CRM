"""System mail (scheduled report deliveries). Configured with SMTP_* settings; without SMTP_HOST nothing is sent
and callers fall back to in-app notifications."""
from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from app.core.config import settings


class MailError(RuntimeError):
    pass


def configured() -> bool:
    return bool(settings.smtp_host)


def _send(msg: EmailMessage) -> None:
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as s:
        if settings.smtp_starttls:
            s.starttls()
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password or "")
        s.send_message(msg)


async def send(to: str, subject: str, text: str, attachments: list[tuple[str, bytes, str]] = ()) -> None:
    """Send one message; attachments are (filename, content, mime type). Raises MailError on any failure."""
    if not configured():
        raise MailError("System email isn't configured")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.smtp_from, to, subject
    msg.set_content(text)
    for name, content, mime in attachments:
        main, _, sub = mime.partition("/")
        msg.add_attachment(content, maintype=main, subtype=sub or "octet-stream", filename=name)
    try:
        await asyncio.to_thread(_send, msg)
    except (OSError, smtplib.SMTPException) as e:
        raise MailError(type(e).__name__) from e  # never the server's reply text: it can echo addresses or credentials

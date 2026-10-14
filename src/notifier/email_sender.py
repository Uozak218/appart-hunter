"""Optional SMTP sender for the contact-message flow.

Most listings on LBC/SeLoger go through the site's own messaging form with
auth + captcha; we can NOT automate those cleanly. But some sources (PAP
particulier, Bien'ici, some LBC pros) expose a real email in the listing
description. When we detect one and ``AUTO_SEND_EMAIL=true`` is set, we send
the drafted message directly via SMTP.

Configure SMTP via env:
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=465
  SMTP_USER=youremail@gmail.com
  SMTP_PASSWORD=<gmail app-password>
  SMTP_FROM_NAME=Sébastien
  SMTP_FROM_EMAIL=youremail@gmail.com
"""

from __future__ import annotations

import re
import smtplib
from email.message import EmailMessage

from src.config import get_settings
from src.models.listing import Listing
from src.utils.logging import logger

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")


def try_extract_email(listing: Listing) -> str | None:
    """Best-effort email extraction from title + description.

    Returns None if no real email is visible (most LBC/SeLoger listings).
    """
    blob = " ".join(filter(None, [listing.title, listing.description, listing.address]))
    match = EMAIL_RE.search(blob or "")
    if not match:
        return None
    email = match.group(0)
    # Reject obvious site-owned emails and role addresses we don't want to target.
    blacklist_fragments = (
        "@leboncoin.fr",
        "@seloger.com",
        "@pap.fr",
        "@bienici.com",
        "@logic-immo.com",
        "noreply@",
        "no-reply@",
    )
    if any(fragment in email.lower() for fragment in blacklist_fragments):
        return None
    return email


def send_email(*, to: str, subject: str, body: str) -> None:
    settings = get_settings()
    if not (settings.smtp_host and settings.smtp_user and settings.smtp_password):
        raise RuntimeError("SMTP not configured (SMTP_HOST / SMTP_USER / SMTP_PASSWORD)")

    msg = EmailMessage()
    msg["Subject"] = subject
    from_addr = settings.smtp_from_email or settings.smtp_user
    from_name = settings.smtp_from_name or from_addr
    msg["From"] = f"{from_name} <{from_addr}>"
    msg["To"] = to
    msg.set_content(body)

    logger.info(f"Sending contact email to {to} via {settings.smtp_host}")
    if settings.smtp_port == 465:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20) as s:
            s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
            s.starttls()
            s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)

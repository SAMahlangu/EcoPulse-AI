"""Resend or SMTP delivery for existing resident notification events."""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

import httpx

from .security import postgres_connection

log = logging.getLogger(__name__)


def _smtp_login(server, username: str, password: str) -> None:
    server.ehlo()
    if "LOGIN" in server.esmtp_features.get("auth", "").upper().split():
        # Gmail's challenge/response LOGIN worked where inline PLAIN disconnected.
        server.user = username
        server.password = password
        server.auth("LOGIN", server.auth_login, initial_response_ok=False)
    else:
        server.login(username, password, initial_response_ok=False)


def send_user_notification_email(user_id: str, subject: str, body: str, preference: str) -> None:
    """Send an email when a provider is configured and the resident opted in.

    This runs as a FastAPI background task. Email addresses and SMTP details
    remain server-side and are never returned or written to application logs.
    """
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    host = os.getenv("SMTP_HOST", "").strip()
    sender = os.getenv("RESEND_FROM", "").strip() if api_key else os.getenv("SMTP_FROM", "").strip()
    if not sender or (not api_key and not host):
        log.warning("Email provider is not configured; set RESEND_API_KEY and RESEND_FROM, or SMTP_HOST and SMTP_FROM")
        return

    try:
        with postgres_connection() as con:
            recipient = con.execute("""select u.email,p.notification_preferences
              from auth.users u join public.profiles p on p.id=u.id
              where u.id=%s and p.role='resident' and coalesce(p.account_status,'active')='active'""", (user_id,)).fetchone()
        if not recipient or not recipient["email"]:
            return
        preferences = recipient["notification_preferences"] or {}
        if preferences.get(preference, True) is False:
            return

        text = f"{body.strip()}\n\nSign in to EcoPulse to view your updates."
        if api_key:
            response = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"from": sender, "to": [recipient["email"]], "subject": subject[:180], "text": text},
                timeout=float(os.getenv("RESEND_TIMEOUT_SECONDS", "10")),
            )
            if not response.is_success:
                # Provider response bodies can contain personal data; log only status.
                log.error("Resend rejected email notification (HTTP %s)", response.status_code)
                return
            log.info("Resend accepted email notification")
            return

        message = EmailMessage()
        message["Subject"] = subject[:180]
        message["From"] = sender
        message["To"] = recipient["email"]
        message.set_content(text)

        port = int(os.getenv("SMTP_PORT", "587"))
        username = os.getenv("SMTP_USERNAME", "").strip()
        password = os.getenv("SMTP_PASSWORD", "")
        timeout = float(os.getenv("SMTP_TIMEOUT_SECONDS", "10"))
        context = ssl.create_default_context()
        if os.getenv("SMTP_USE_SSL", "false").lower() == "true":
            with smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) as server:
                if username:
                    _smtp_login(server, username, password)
                server.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                if username:
                    _smtp_login(server, username, password)
                server.send_message(message)
        log.info("SMTP accepted email notification")
    except smtplib.SMTPAuthenticationError as error:
        log.error("Email notification authentication rejected (SMTP %s)", error.smtp_code)
    except Exception as error:
        # Delivery failure must not undo the in-app notification or API update.
        log.error("Email notification delivery failed (%s)", type(error).__name__)

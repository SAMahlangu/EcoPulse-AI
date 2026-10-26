"""Resend or SMTP delivery for existing resident notification events."""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from dataclasses import dataclass

import httpx

from .security import postgres_connection

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryResult:
    """Provider acceptance is distinct from delivery to an inbox."""
    status: str
    provider: str | None = None
    provider_message_id: str | None = None
    error_code: str | None = None
    retry_after_seconds: int | None = None


def _timeout(name: str) -> float:
    try:
        return max(1, min(30, float(os.getenv(name, "10"))))
    except ValueError:
        return 10


def _smtp_login(server, username: str, password: str) -> None:
    server.ehlo()
    if "LOGIN" in server.esmtp_features.get("auth", "").upper().split():
        # Gmail's challenge/response LOGIN worked where inline PLAIN disconnected.
        server.user = username
        server.password = password
        server.auth("LOGIN", server.auth_login, initial_response_ok=False)
    else:
        server.login(username, password, initial_response_ok=False)


def send_user_notification_email(user_id: str, subject: str, body: str, preference: str,
                                 *, idempotency_key: str | None = None) -> DeliveryResult:
    """Send an email when a provider is configured and the resident opted in.

    Called by the durable notification worker. Recheck preferences and account
    state at send time. Return only safe status codes, never provider bodies.
    """
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    host = os.getenv("SMTP_HOST", "").strip()
    sender = os.getenv("RESEND_FROM", "").strip() if api_key else os.getenv("SMTP_FROM", "").strip()
    if not sender or (not api_key and not host):
        log.warning("Email provider is not configured; set RESEND_API_KEY and RESEND_FROM, or SMTP_HOST and SMTP_FROM")
        return DeliveryResult("retry", error_code="provider_not_configured")

    try:
        with postgres_connection() as con:
            recipient = con.execute("""select u.email,p.notification_preferences
              from auth.users u join public.profiles p on p.id=u.id
              where u.id=%s and p.role='resident' and coalesce(p.account_status,'active')='active'""", (user_id,)).fetchone()
        if not recipient or not recipient["email"]:
            return DeliveryResult("skipped", error_code="recipient_unavailable")
        preferences = recipient["notification_preferences"] or {}
        if preferences.get(preference, True) is False:
            return DeliveryResult("skipped", error_code="preference_disabled")

        text = f"{body.strip()}\n\nSign in to EcoPulse to view your updates."
        if api_key:
            headers = {"Authorization": f"Bearer {api_key}"}
            if idempotency_key:
                headers["Idempotency-Key"] = idempotency_key
            response = httpx.post(
                "https://api.resend.com/emails",
                headers=headers,
                json={"from": sender, "to": [recipient["email"]], "subject": subject[:180], "text": text},
                timeout=_timeout("RESEND_TIMEOUT_SECONDS"),
            )
            if not response.is_success:
                # Provider response bodies can contain personal data; log only status.
                log.error("Resend rejected email notification (HTTP %s)", response.status_code)
                retry_after = response.headers.get("Retry-After", "")
                retry_seconds = min(3600, max(1, int(retry_after))) if retry_after.isdigit() else None
                transient = response.status_code in {408, 409, 425, 429} or response.status_code >= 500
                return DeliveryResult("retry" if transient else "failed", "resend",
                                      error_code=f"http_{response.status_code}", retry_after_seconds=retry_seconds)
            log.info("Resend accepted email notification")
            try:
                message_id = response.json().get("id")
            except (ValueError, AttributeError):
                message_id = None
            return DeliveryResult("accepted", "resend", str(message_id)[:255] if message_id else None)

        message = EmailMessage()
        message["Subject"] = subject[:180]
        message["From"] = sender
        message["To"] = recipient["email"]
        if idempotency_key:
            # Stable Message-ID aids tracing; SMTP cannot guarantee deduplication.
            message["Message-ID"] = f"<{idempotency_key.replace(':', '.')}@notifications.ecopulse.invalid>"
        message.set_content(text)

        port = int(os.getenv("SMTP_PORT", "587"))
        username = os.getenv("SMTP_USERNAME", "").strip()
        password = os.getenv("SMTP_PASSWORD", "")
        timeout = _timeout("SMTP_TIMEOUT_SECONDS")
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
        return DeliveryResult("accepted", "smtp")
    except smtplib.SMTPAuthenticationError as error:
        log.error("Email notification authentication rejected (SMTP %s)", error.smtp_code)
        return DeliveryResult("failed", "smtp", error_code="smtp_authentication_rejected")
    except smtplib.SMTPResponseException as error:
        log.error("Email notification rejected (SMTP %s)", error.smtp_code)
        return DeliveryResult("retry" if 400 <= error.smtp_code < 500 else "failed", "smtp",
                              error_code=f"smtp_{error.smtp_code}")
    except Exception as error:
        # Delivery failure must not undo the in-app notification or API update.
        log.error("Email notification delivery failed (%s)", type(error).__name__)
        return DeliveryResult("retry", "resend" if api_key else "smtp", error_code="delivery_unavailable")

from contextlib import contextmanager
from unittest.mock import MagicMock, Mock

import httpx
import pytest

from backend.app import email_notifications as emails


@pytest.fixture
def delivery(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setenv("RESEND_FROM", "EcoPulse <updates@example.com>")
    recipient = {"email": "resident@example.com", "notification_preferences": {}}
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = recipient

    @contextmanager
    def database():
        yield connection

    monkeypatch.setattr(emails, "postgres_connection", database)
    post = Mock(return_value=httpx.Response(200, json={"id": "email-id"}))
    monkeypatch.setattr(emails.httpx, "post", post)
    return recipient, post


def test_resend_delivers_notification_to_account_email(delivery):
    _, post = delivery
    emails.send_user_notification_email("user-id", "Report update", "Repair complete", "incident_updates")
    assert post.call_args.args == ("https://api.resend.com/emails",)
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"
    payload = post.call_args.kwargs["json"]
    assert payload["to"] == ["resident@example.com"]
    assert payload["from"] == "EcoPulse <updates@example.com>"
    assert payload["subject"] == "Report update"
    assert "Repair complete" in payload["text"]


def test_resend_respects_notification_opt_out(delivery):
    recipient, post = delivery
    recipient["notification_preferences"] = {"incident_updates": False}
    emails.send_user_notification_email("user-id", "Update", "Body", "incident_updates")
    post.assert_not_called()


@pytest.mark.parametrize("failure", [403, 429, 500, "timeout"])
def test_provider_failure_does_not_break_update_or_log_private_data(delivery, caplog, failure):
    _, post = delivery
    if failure == "timeout":
        post.side_effect = httpx.TimeoutException("resident@example.com test-key")
    else:
        post.return_value = httpx.Response(failure, text="resident@example.com test-key")
    emails.send_user_notification_email("user-id", "Update", "Body", "incident_updates")
    assert "resident@example.com" not in caplog.text
    assert "test-key" not in caplog.text
    assert "failed" in caplog.text or "rejected" in caplog.text


def test_smtp_remains_available_without_resend_key(delivery, monkeypatch):
    _, post = delivery
    monkeypatch.delenv("RESEND_API_KEY")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "updates@example.com")
    monkeypatch.setenv("SMTP_USE_SSL", "false")
    smtp = MagicMock()
    monkeypatch.setattr(emails.smtplib, "SMTP", smtp)
    emails.send_user_notification_email("user-id", "Update", "Body", "incident_updates")
    post.assert_not_called()
    smtp.return_value.__enter__.return_value.send_message.assert_called_once()


@pytest.mark.parametrize("use_ssl", [True, False])
def test_smtp_alert_uses_working_gmail_login(delivery, monkeypatch, use_ssl):
    _, post = delivery
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_FROM", "EcoPulse <sender@example.com>")
    monkeypatch.setenv("SMTP_USERNAME", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "test-app-password")
    monkeypatch.setenv("SMTP_USE_SSL", str(use_ssl).lower())
    smtp = MagicMock()
    server = smtp.return_value.__enter__.return_value
    server.esmtp_features = {"auth": "LOGIN PLAIN XOAUTH2"}
    monkeypatch.setattr(emails.smtplib, "SMTP_SSL" if use_ssl else "SMTP", smtp)
    emails.send_user_notification_email("user-id", "Service alert", "Water restored", "municipal_notices")
    server.auth.assert_called_once_with("LOGIN", server.auth_login, initial_response_ok=False)
    server.login.assert_not_called()
    assert server.user == "sender@example.com"
    message = server.send_message.call_args.args[0]
    assert message["To"] == "resident@example.com"
    assert "Water restored" in message.get_content()
    if not use_ssl:
        server.starttls.assert_called_once()
    post.assert_not_called()

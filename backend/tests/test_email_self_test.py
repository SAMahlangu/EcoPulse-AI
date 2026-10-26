import pytest
from unittest.mock import Mock
from fastapi.testclient import TestClient

from backend.app import email_notifications as emails
from backend.app.main import app
from backend.app.security import current_user


@pytest.fixture
def client(monkeypatch):
    user = {"id": "resident-id", "role": "resident"}
    monkeypatch.setattr(emails, "_test_email_attempts", {})
    app.dependency_overrides[current_user] = lambda: user
    with TestClient(app) as test_client:
        yield test_client, user
    app.dependency_overrides.clear()


def test_email_self_test_sends_only_to_signed_in_resident(client, monkeypatch):
    test_client, user = client
    send = Mock(return_value="accepted")
    monkeypatch.setattr(emails, "send_user_notification_email", send)

    response = test_client.post("/api/v1/email/test")

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert "Check your inbox" in response.json()["message"]
    assert user["id"] in emails._test_email_attempts
    send.assert_called_once_with(
        user["id"],
        "EcoPulse email delivery test",
        "This is a test of EcoPulse email notifications requested from your account.",
        "municipal_notices",
    )


@pytest.mark.parametrize(
    ("status", "http_status"),
    [
        ("not_configured", 503),
        ("no_recipient", 409),
        ("opted_out", 409),
        ("rejected", 502),
        ("failed", 502),
    ],
)
def test_email_self_test_reports_delivery_failures(client, monkeypatch, status, http_status):
    test_client, _ = client
    monkeypatch.setattr(emails, "send_user_notification_email", lambda *args: status)

    response = test_client.post("/api/v1/email/test")

    assert response.status_code == http_status
    assert response.json()["detail"]


def test_email_self_test_requires_resident_role(client, monkeypatch):
    test_client, _ = client
    app.dependency_overrides[current_user] = lambda: {"id": "official-id", "role": "municipal"}
    send = lambda *args: pytest.fail("email must not be sent to non-resident accounts")
    monkeypatch.setattr(emails, "send_user_notification_email", send)

    assert test_client.post("/api/v1/email/test").status_code == 403


def test_email_self_test_requires_authentication():
    assert TestClient(app).post("/api/v1/email/test").status_code == 401


def test_email_self_test_is_rate_limited(client, monkeypatch):
    test_client, _ = client
    send = lambda *args: "accepted"
    monkeypatch.setattr(emails, "send_user_notification_email", send)

    assert test_client.post("/api/v1/email/test").status_code == 200
    response = test_client.post("/api/v1/email/test")

    assert response.status_code == 429
    assert response.headers["Retry-After"]

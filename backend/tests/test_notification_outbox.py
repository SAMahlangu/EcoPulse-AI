import asyncio
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import notification_delivery
from backend.app.email_notifications import DeliveryResult
from backend.app.security import current_user
from backend.app.services import notification_outbox as outbox
from backend.app.services import notification_worker as worker


def test_worker_sends_with_stable_key_and_persists_provider_acceptance(monkeypatch):
    job = {"id":"job-id","user_id":"resident-id","subject":"Subject","body":"Private body","preference":"incident_updates","attempts":1,"max_attempts":6,"claim_token":"token"}
    monkeypatch.setattr(outbox,"claim_notification",lambda:job)
    send = Mock(return_value=DeliveryResult("accepted","resend","provider-id"))
    finish = Mock()
    monkeypatch.setattr(outbox,"send_user_notification_email",send)
    monkeypatch.setattr(outbox,"finish_notification",finish)
    assert outbox.deliver_one()
    assert send.call_args.kwargs == {"idempotency_key":"notification:job-id"}
    assert finish.call_args.args[1].status == "accepted"


def test_worker_does_not_contact_provider_without_a_job(monkeypatch):
    monkeypatch.setattr(outbox,"claim_notification",lambda:None)
    send = Mock()
    monkeypatch.setattr(outbox,"send_user_notification_email",send)
    assert not outbox.deliver_one()
    send.assert_not_called()


@pytest.mark.parametrize("attempt,expected",[(1,30),(2,60),(6,960),(15,3600)])
def test_retry_backoff_is_bounded(attempt,expected):
    assert outbox.retry_delay(attempt) == expected
    assert outbox.retry_delay(attempt,99999) == 3600


@pytest.mark.parametrize("attempt,expected",[(1,"retry"),(6,"failed")])
def test_failed_attempt_has_budget_and_claim_fencing(monkeypatch,attempt,expected):
    con = Mock()
    @contextmanager
    def database():
        yield con
    monkeypatch.setattr(outbox,"postgres_connection",database)
    outbox.finish_notification({"id":"job-id","claim_token":"owned-claim","attempts":attempt,"max_attempts":6},
                               DeliveryResult("retry","resend",error_code="http_503"))
    sql, params = con.execute.call_args.args
    assert params[0] == expected
    assert params[-2:] == ("job-id","owned-claim")
    assert "claim_token=%s" in sql and "status='processing'" in sql


def test_enqueue_uses_callers_transaction_and_stable_event(monkeypatch):
    con = Mock()
    con.execute.return_value.fetchone.return_value = {"id":"job-id"}
    own_connection = Mock(side_effect=AssertionError("enqueue must not open/commit another connection"))
    monkeypatch.setattr(outbox,"postgres_connection",own_connection)
    assert outbox.enqueue_notification(con,"resident-id","Subject","Body","incident_updates",event_key="incident:event") == "job-id"
    assert con.execute.call_args.args[1] == ("resident-id","incident:event","Subject","Body","incident_updates")
    con.commit.assert_not_called()
    own_connection.assert_not_called()
    con.execute.return_value.fetchone.return_value = None
    assert outbox.enqueue_notification(con,"resident-id","Subject","Body","incident_updates",event_key="incident:event") is None


def test_invalid_event_is_not_queued():
    con=Mock()
    with pytest.raises(ValueError):
        outbox.enqueue_notification(con,"resident-id","Subject","Body","unknown",event_key="event")
    con.execute.assert_not_called()


def test_worker_disabled_without_database(monkeypatch):
    monkeypatch.setenv("ECOPULSE_NOTIFICATION_WORKER_ENABLED","true")
    monkeypatch.setattr(worker,"database_url",lambda:"")
    send=Mock(side_effect=AssertionError("must not access delivery without a database"))
    monkeypatch.setattr(worker,"deliver_one",send)
    asyncio.run(worker.notification_worker(asyncio.Event()))
    send.assert_not_called()


def test_admin_delivery_is_not_accessible_to_residents():
    app=FastAPI()
    app.include_router(notification_delivery.router)
    client=TestClient(app)
    assert client.get("/api/v1/admin/notification-delivery").status_code == 401
    app.dependency_overrides[current_user]=lambda:{"id":"resident-id","role":"resident"}
    assert client.get("/api/v1/admin/notification-delivery").status_code == 403


def test_admin_cannot_retry_provider_accepted_mail(monkeypatch):
    con=Mock()
    con.execute.return_value.fetchone.return_value=None
    @contextmanager
    def database():
        yield con
    monkeypatch.setattr(notification_delivery,"postgres_connection",database)
    with pytest.raises(Exception) as error:
        notification_delivery.retry_delivery(__import__('uuid').uuid4(),user={"id":"admin-id"})
    assert error.value.status_code == 409
    assert "status in ('failed','skipped')" in con.execute.call_args.args[0]

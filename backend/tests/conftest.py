"""Tests never dispatch real notification jobs from the app lifespan."""
import pytest


@pytest.fixture(autouse=True)
def disable_live_notification_worker(monkeypatch):
    monkeypatch.setenv("ECOPULSE_NOTIFICATION_WORKER_ENABLED", "false")

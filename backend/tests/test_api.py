from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.app.main.DB_PATH", tmp_path / "test.sqlite3")
    monkeypatch.setenv("ECOPULSE_LOCAL_DEMO", "true")
    with TestClient(app) as test_client:
        yield test_client


def test_health(client):
    assert client.get("/api/v1/health").json()["status"] == "ok"


def test_privileged_and_account_routes_require_a_verified_session(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/municipal/dashboard").status_code == 401
    assert client.get("/api/v1/admin/dashboard").status_code == 401


def test_local_sqlite_data_is_disabled_without_explicit_demo_flag(client, monkeypatch):
    monkeypatch.setenv("ECOPULSE_LOCAL_DEMO", "false")
    assert client.get("/api/v1/resident/dashboard").status_code == 503


def test_water_reading_is_persisted_and_calculated(client):
    response = client.post("/api/v1/water/readings", json={
        "previous_reading": 10.25, "current_reading": 12.75,
        "previous_date": "2026-09-01", "current_date": "2026-10-01", "household_size": 3,
    })
    assert response.status_code == 200
    assert response.json()["consumption"] == 2.5
    assert client.get("/api/v1/water/readings").json()["count"] == 1


def test_reading_rejects_decreasing_meter(client):
    response = client.post("/api/v1/energy/readings", json={
        "previous_reading": 20, "current_reading": 19,
        "previous_date": "2026-09-01", "current_date": "2026-10-01",
    })
    assert response.status_code == 422


def test_incident_submission_and_pagination(client):
    response = client.post("/api/v1/incidents", json={
        "category": "water", "title": "Water leak on street",
        "description": "A visible leak has been flowing since this morning.", "location": "Hatfield, Pretoria",
    })
    assert response.status_code == 200
    assert response.json()["status"] == "submitted"
    page = client.get("/api/v1/incidents?limit=1&offset=0").json()
    assert len(page["items"]) == 1


def test_image_analysis_requires_a_signed_in_resident(client):
    response = client.post("/api/v1/incidents/analyze-image", files={"image": ("incident.jpg", b"not-an-image", "image/jpeg")})
    assert response.status_code == 401


def test_reading_rejects_bad_date_order(client):
    response = client.post("/api/v1/water/readings", json={
        "previous_reading": 1, "current_reading": 2,
        "previous_date": date(2026, 10, 2).isoformat(), "current_date": date(2026, 10, 1).isoformat(),
    })
    assert response.status_code == 422


def test_dashboard_aggregates_recorded_values_only(client):
    today = datetime.now(timezone.utc).date()
    previous = today.replace(day=1)
    if previous == today:
        previous = date(today.year - 1, 12, 1) if today.month == 1 else date(today.year, today.month - 1, 1)
    client.post("/api/v1/water/readings", json={
        "previous_reading": 10, "current_reading": 12.5,
        "previous_date": previous.isoformat(), "current_date": today.isoformat(), "household_size": 2,
    })
    client.post("/api/v1/energy/readings", json={
        "previous_reading": 100, "current_reading": 230,
        "previous_date": previous.isoformat(), "current_date": today.isoformat(),
    })
    client.post("/api/v1/incidents", json={
        "category": "water", "title": "Street leak",
        "description": "Water is leaking from the pavement outside.", "location": "Hatfield",
    })

    response = client.get("/api/v1/resident/dashboard")
    assert response.status_code == 200
    payload = response.json()
    assert payload["active_incidents"]["value"] == 1
    assert payload["water"]["consumption_kilolitres"] == 2.5
    assert payload["energy"]["consumption_kwh"] == 130
    assert len(payload["recent_incidents"]) == 1

"""Water API boundary, persistence and simulation isolation tests without live secrets."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
import pytest

from backend.app import water
from backend.app.security import current_user

OWNER = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"
HOUSEHOLD = "00000000-0000-0000-0000-000000000003"
SETTINGS = {"id": HOUSEHOLD, "owner_id": OWNER, "name": "Test household", "household_size": 4,
            "daily_target_litres": 600, "tariff_per_kl": 20, "timezone": "Africa/Johannesburg"}
STAMP = datetime(2026, 10, 25, 8, tzinfo=timezone.utc)


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(water.router)
    @application.exception_handler(RequestValidationError)
    async def invalid_water_input(_request, _exc):
        return water.validation_error_response()
    water._analysis_attempts.clear()
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
def client(app):
    app.dependency_overrides[current_user] = lambda: {"id": OWNER, "role": "resident"}
    with TestClient(app) as client:
        yield client


class Cursor:
    def __init__(self, row=None, rows=None):
        self.row, self.rows = row, rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self):
        self.calls, self.sessions = [], {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if sql.startswith("set local"):
            return Cursor()
        if "insert into public.water_simulation_sessions" in sql:
            identifier, user_id, scenario, payload = params
            row = {"id": identifier, "user_id": user_id, "scenario": scenario, "payload": json.loads(payload),
                   "created_at": STAMP, "updated_at": STAMP}
            self.sessions[identifier] = row
            return Cursor(deepcopy(row))
        if "from public.water_simulation_sessions s" in sql:
            if "s.id=%s" in sql:
                row = self.sessions.get(str(params[0]))
                return Cursor(deepcopy(row) if row and row["user_id"] == params[1] else None)
            rows = [row for row in self.sessions.values() if row["user_id"] == params[0]]
            return Cursor(deepcopy(rows[-1]) if rows else None)
        if sql.startswith("update public.water_simulation_sessions"):
            payload, identifier, user_id = params
            if identifier in self.sessions and self.sessions[identifier]["user_id"] == user_id:
                self.sessions[identifier]["payload"] = json.loads(payload)
            return Cursor()
        if sql.startswith("delete from public.water_simulation_sessions"):
            identifier, user_id = params
            if identifier in self.sessions and self.sessions[identifier]["user_id"] == user_id:
                del self.sessions[identifier]
            return Cursor()
        pytest.fail(f"Unexpected database statement: {sql[:120]}")


def test_every_water_endpoint_uses_existing_authentication(app):
    identifier = str(uuid4())
    with TestClient(app) as client:
        for path in ("household", "dashboard", "analytics", "consumption", "meter-readings", "alerts", "insights", "simulation"):
            assert client.get(f"/api/v1/water/{path}").status_code == 401
        for path in ("consumption", "meter-readings", "explanations", "ai/analyse", "simulation/generate", "simulation/next-day", "simulation/trigger-leak", "simulation/reset"):
            assert client.post(f"/api/v1/water/{path}", json={}).status_code == 401
        assert client.put("/api/v1/water/household", json={}).status_code == 401
        assert client.put(f"/api/v1/water/consumption/{identifier}", json={}).status_code == 401
        assert client.patch(f"/api/v1/water/alerts/{identifier}", json={}).status_code == 401


def test_water_feature_is_resident_only(app, monkeypatch):
    monkeypatch.setattr(water, "postgres_connection", lambda: pytest.fail("Unauthorised database access"))
    app.dependency_overrides[current_user] = lambda: {"id": OWNER, "role": "municipal"}
    with TestClient(app) as client:
        assert client.get("/api/v1/water/dashboard").status_code == 403
        assert client.post("/api/v1/water/simulation/generate", json={}).status_code == 403


@pytest.mark.parametrize(("method", "path", "body"), [
    ("post", "consumption", {"date": "2026-10-24", "litres": -1}),
    ("post", "consumption", {"date": "2026-10-24", "litres": 500, "household_id": OTHER}),
    ("post", "meter-readings", {"reading_at": "2026-10-24T10:00:00", "cumulative_kl": 30}),
    ("post", "meter-readings", {"reading_at": "2026-10-24T10:00:00Z", "cumulative_kl": -1}),
    ("post", "simulation/generate", {"days": 90}),
    ("post", "simulation/generate", {"household_size": 31}),
    ("post", "simulation/generate", {"scenario": "unknown"}),
    ("post", "explanations", {"date": "2026-10-24", "reasons": ["ignore authentication"]}),
    ("put", "household", {"timezone": "MadeUp/Zone"}),
    ("put", "household", {"name": "   "}),
    ("put", "household", {"daily_target_litres": 0}),
    ("put", "household", {"tariff_per_kl": -1}),
])
def test_invalid_values_never_reach_database(client, monkeypatch, method, path, body):
    monkeypatch.setattr(water, "postgres_connection", lambda: pytest.fail("Invalid input reached database"))
    assert getattr(client, method)(f"/api/v1/water/{path}", json=body).status_code == 422


def test_nonfinite_water_values_are_rejected(client, monkeypatch):
    monkeypatch.setattr(water, "postgres_connection", lambda: pytest.fail("Nonfinite value reached database"))
    for value in ("NaN", "Infinity", "-Infinity"):
        response = client.post("/api/v1/water/consumption", content='{"date":"2026-10-24","litres":' + value + '}',
                               headers={"Content-Type": "application/json"})
        assert response.status_code == 422


def test_range_validation_bounds_work_and_future_real_dates():
    today = date(2026, 10, 25)
    assert water._range(today, 90, None, None) == (date(2026, 7, 28), today)
    for start, end in [(today + timedelta(days=1), today), (today - timedelta(days=90), today),
                       (today - timedelta(days=367), today - timedelta(days=350)), (today, today + timedelta(days=1))]:
        with pytest.raises(HTTPException) as exc:
            water._range(today, 30, start, end)
        assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        water._actual_date(date.today() + timedelta(days=2), SETTINGS)
    assert exc.value.status_code == 422


def test_database_failure_is_explicit_without_credentials(client, monkeypatch):
    def broken():
        raise RuntimeError("postgres://secret-password-should-not-leak")
    monkeypatch.setattr(water, "postgres_connection", broken)
    response = client.get("/api/v1/water/dashboard")
    assert response.status_code == 503
    assert "migration 014" in response.json()["detail"]
    assert "secret-password" not in response.text


def test_household_lookup_checks_owner_role_status_and_serialises_writes():
    calls = []
    class Connection:
        def execute(self, sql, params):
            calls.append((sql, params))
            return Cursor(SETTINGS)
    assert water._household(Connection(), {"id": OWNER}, lock=True)["owner_id"] == OWNER
    assert calls[0][1] == (OWNER,)
    assert "h.owner_id=%s" in calls[0][0] and "p.role='resident'" in calls[0][0]
    assert "account_status" in calls[0][0] and "for update of h" in calls[0][0]


def test_missing_or_inactive_household_cannot_be_authorised_by_payload():
    class Connection:
        def execute(self, sql, params):
            assert params == (OWNER,)
            return Cursor()
    with pytest.raises(HTTPException) as exc:
        water._household(Connection(), {"id": OWNER}, lock=True)
    assert exc.value.status_code == 403


def test_meter_reset_and_neighbor_chronology():
    timestamp = datetime.now(timezone.utc) - timedelta(days=2)
    class Connection:
        def __init__(self, previous, following):
            self.rows, self.calls = iter([previous, following]), []
        def execute(self, sql, params):
            self.calls.append((sql, params))
            assert params == (HOUSEHOLD, timestamp)
            return Cursor(next(self.rows))
    form = water.MeterInput(reading_at=timestamp, cumulative_kl=10)
    with pytest.raises(HTTPException, match="decreased"):
        water._validate_meter_neighbors(Connection({"cumulative_kl": 20}, None), SETTINGS, form)
    water._validate_meter_neighbors(Connection({"cumulative_kl": 20}, {"cumulative_kl": 11, "is_reset": False}),
                                    SETTINGS, form.model_copy(update={"is_reset": True}))
    with pytest.raises(HTTPException, match="above the next"):
        water._validate_meter_neighbors(Connection({"cumulative_kl": 9}, {"cumulative_kl": 9.5, "is_reset": False}), SETTINGS, form)
    water._validate_meter_neighbors(Connection({"cumulative_kl": 9}, {"cumulative_kl": 1, "is_reset": True}), SETTINGS, form)


def test_edit_daily_record_scopes_id_to_current_household(client, monkeypatch):
    calls = []
    class Connection(FakeConnection):
        def execute(self, sql, params=()):
            if sql.startswith("set local"):
                return Cursor()
            calls.append((sql, params))
            return Cursor()
    monkeypatch.setattr(water, "postgres_connection", Connection)
    monkeypatch.setattr(water, "_household", lambda *_args, **_kwargs: SETTINGS)
    response = client.put(f"/api/v1/water/consumption/{uuid4()}", json={"date": date.today().isoformat(), "litres": 600})
    assert response.status_code == 404
    assert "where id=%s and household_id=%s" in calls[0][0]
    assert calls[0][1][-1] == HOUSEHOLD


def test_reports_and_alerts_deduplicate_while_real_corrections_update_original():
    state = {"reports": {}, "alerts": {}, "notifications": [], "calls": []}
    class Connection:
        def execute(self, sql, params=()):
            state["calls"].append((sql, params))
            if "insert into public.water_alerts" in sql:
                state["alerts"][params[:3]] = params[3:]
            elif "insert into public.water_daily_reports" in sql:
                key = params[:2]
                if key not in state["reports"]:
                    state["reports"][key] = params[2]
                    return Cursor({"id": "report"})
            elif "insert into public.notifications" in sql:
                state["notifications"].append(params)
                return Cursor({"id": "notification"})
            return Cursor()
    report = {"date": "2026-10-25", "litres": 850, "target_litres": 600, "difference_litres": 250,
              "status": "Above Target", "is_estimated": False, "is_simulation": False}
    alert = {"consumption_date": "2026-10-25", "alert_type": "above_target", "severity": "warning", "message": "High use"}
    dashboard = {"is_simulation": False, "daily_reports": [report], "alerts": [alert]}
    water._persist_reports(Connection(), SETTINGS, dashboard)
    water._persist_reports(Connection(), SETTINGS, dashboard)
    assert len(state["reports"]) == len(state["alerts"]) == len(state["notifications"]) == 1
    assert any("update public.notifications" in sql for sql, _ in state["calls"])
    assert "Open Water Management" in state["notifications"][0][1]
    before = len(state["calls"])
    water._persist_reports(Connection(), SETTINGS, {**dashboard, "is_simulation": True})
    assert len(state["calls"]) == before
    water._persist_reports(Connection(), SETTINGS, {"daily_reports": [{**report, "litres": None}], "alerts": []})
    assert len(state["notifications"]) == 1


def test_simulation_flow_is_persistent_owner_scoped_cached_and_separate(client, monkeypatch, app):
    connection = FakeConnection()
    monkeypatch.setattr(water, "postgres_connection", lambda: connection)
    monkeypatch.setattr(water, "_household", lambda *_args, **_kwargs: deepcopy(SETTINGS))
    monkeypatch.setattr(water, "_today", lambda _: date(2026, 10, 25))
    analyses = []
    def advisor(evidence, explanation=None):
        analyses.append((deepcopy(evidence), deepcopy(explanation)))
        return {"source": "gemini", "model": "test-model", "generated_at": STAMP.isoformat(),
                "analysis": {"summary": "Review this illustrative increase.", "possible_causes": [],
                             "recommendations": ["Inspect visible pipes."], "caveat": "Simulation cannot confirm real leaks."}}
    monkeypatch.setattr(water, "analyse_water", advisor)
    response = client.post("/api/v1/water/simulation/generate", json={"days": 30, "scenario": "normal"})
    assert response.status_code == 201
    dashboard = response.json()
    identifier = dashboard["session_id"]
    assert len(dashboard["series"]) == 30 and dashboard["is_simulation"]
    assert client.get("/api/v1/water/simulation").json()["session_id"] == identifier
    leaked = client.post("/api/v1/water/simulation/trigger-leak", json={"session_id": identifier}).json()
    assert leaked["summary"]["today_litres"] >= 1440
    assert any(alert["alert_type"] == "possible_leak" for alert in leaked["alerts"])
    last = leaked["as_of_date"]
    body = {"date": last, "session_id": identifier}
    first_analysis = client.post("/api/v1/water/ai/analyse", json=body)
    assert first_analysis.status_code == 200
    assert client.post("/api/v1/water/ai/analyse", json=body).json()["id"] == first_analysis.json()["id"]
    assert len(analyses) == 1
    explained = client.post("/api/v1/water/explanations", json={**body, "reasons": ["visitors", "laundry"], "text": "We had visitors."})
    assert explained.status_code == 200 and len(analyses) == 2
    assert explained.json()["explanation"]["reasons"] == ["visitors", "laundry"]
    assert analyses[-1][1]["text"] == "We had visitors."
    stored = client.get(f"/api/v1/water/insights?date={last}&session_id={identifier}").json()
    assert stored["explanation"]["text"] == "We had visitors." and len(stored["items"]) == 1
    alert = next(row for row in leaked["alerts"] if row["alert_type"] == "possible_leak")
    assert client.patch(f"/api/v1/water/alerts/{alert['id']}?session_id={identifier}", json={"status": "dismissed"}).status_code == 200
    restored = client.get(f"/api/v1/water/dashboard?session_id={identifier}").json()
    assert next(row for row in restored["alerts"] if row["id"] == alert["id"])["status"] == "dismissed"
    assert client.post("/api/v1/water/simulation/next-day", json={"session_id": identifier}).status_code == 200
    app.dependency_overrides[current_user] = lambda: {"id": OTHER, "role": "resident"}
    assert client.get(f"/api/v1/water/dashboard?session_id={identifier}").status_code == 404
    assert client.post("/api/v1/water/simulation/reset", json={"session_id": identifier}).status_code == 404
    app.dependency_overrides[current_user] = lambda: {"id": OWNER, "role": "resident"}
    assert client.post("/api/v1/water/simulation/reset", json={"session_id": identifier}).json()["reset"] is True
    assert client.get("/api/v1/water/simulation").json() is None
    assert all("public.notifications" not in sql and "public.water_consumption_daily" not in sql and "public.water_meter_readings" not in sql for sql, _ in connection.calls)


def test_missing_day_analysis_and_explanation_fail_before_provider(client, monkeypatch):
    session = {"id": str(uuid4()), "scenario": "normal", "created_at": STAMP,
               "payload": {"household": SETTINGS, "records": [{"id": str(uuid4()), "date": "2026-10-25", "litres": 500}]}}
    monkeypatch.setattr(water, "_session", lambda *_args, **_kwargs: deepcopy(session))
    monkeypatch.setattr(water, "postgres_connection", FakeConnection)
    monkeypatch.setattr(water, "analyse_water", lambda *_args: pytest.fail("Provider called without actual data"))
    body = {"date": "2026-10-24", "session_id": session["id"]}
    assert client.post("/api/v1/water/ai/analyse", json=body).status_code == 422
    assert client.post("/api/v1/water/explanations", json=body).status_code == 422


def test_cache_signature_uses_explanation_meaning_and_model_configuration(monkeypatch):
    evidence = {"facts": {"litres": 850}}
    explanation = {"id": str(uuid4()), "updated_at": STAMP.isoformat(), "text": "Laundry", "reasons": ["laundry"]}
    baseline = water._fingerprint(evidence, explanation)
    assert water._fingerprint(evidence, {**explanation, "id": str(uuid4()), "updated_at": "later"}) == baseline
    assert water._fingerprint(evidence, {**explanation, "text": "Visitors"}) != baseline
    assert water._fingerprint({"facts": {"litres": 900}}, explanation) != baseline
    monkeypatch.setenv("GEMINI_MODEL", "a-different-model")
    assert water._fingerprint(evidence, explanation) != baseline


def test_uncached_analysis_rate_limit_is_per_resident():
    water._analysis_attempts.clear()
    for _ in range(8):
        water._limit_analysis(OWNER)
    with pytest.raises(HTTPException) as exc:
        water._limit_analysis(OWNER)
    assert exc.value.status_code == 429
    water._limit_analysis(OTHER)

"""Energy API boundary, persistence and simulation isolation tests without live secrets."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
import pytest

from backend.app import energy
from backend.app.security import current_user

OWNER = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"
HOUSEHOLD = "00000000-0000-0000-0000-000000000003"
SETTINGS = {"id": HOUSEHOLD, "owner_id": OWNER, "name": "Test household", "household_size": 4,
            "daily_target_kwh": 15, "tariff_per_kwh": 2.75, "timezone": "Africa/Johannesburg"}
STAMP = datetime(2026, 10, 25, 8, tzinfo=timezone.utc)


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(energy.router)
    @application.exception_handler(RequestValidationError)
    async def invalid_energy_input(_request, _exc):
        return energy.validation_error_response()
    energy._analysis_attempts.clear()
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
        if "insert into public.energy_simulation_sessions" in sql:
            identifier, user_id, scenario, payload = params
            row = {"id": identifier, "user_id": user_id, "scenario": scenario, "payload": json.loads(payload),
                   "created_at": STAMP, "updated_at": STAMP}
            self.sessions[identifier] = row
            return Cursor(deepcopy(row))
        if "from public.energy_simulation_sessions s" in sql:
            if "s.id=%s" in sql:
                row = self.sessions.get(str(params[0]))
                return Cursor(deepcopy(row) if row and row["user_id"] == params[1] else None)
            rows = [row for row in self.sessions.values() if row["user_id"] == params[0]]
            return Cursor(deepcopy(rows[-1]) if rows else None)
        if sql.startswith("update public.energy_simulation_sessions"):
            payload, identifier, user_id = params
            if identifier in self.sessions and self.sessions[identifier]["user_id"] == user_id:
                self.sessions[identifier]["payload"] = json.loads(payload)
            return Cursor()
        if sql.startswith("delete from public.energy_simulation_sessions"):
            identifier, user_id = params
            if identifier in self.sessions and self.sessions[identifier]["user_id"] == user_id:
                del self.sessions[identifier]
            return Cursor()
        pytest.fail(f"Unexpected database statement: {sql[:120]}")


def test_every_energy_endpoint_uses_existing_authentication(app):
    identifier = str(uuid4())
    with TestClient(app) as client:
        for path in ("household", "settings", "dashboard", "analytics", "consumption", "meter-readings", "alerts", "insights", "simulation", "appliances", "prepaid", "advisor"):
            assert client.get(f"/api/v1/energy/{path}").status_code == 401
        for path in ("consumption", "meter-readings", "explanations", "ai/analyse", "simulation/generate", "simulation/next-day", "simulation/trigger-spike", "simulation/geyser-overuse", "simulation/reset", "appliances", "prepaid/purchases", "prepaid/balances"):
            assert client.post(f"/api/v1/energy/{path}", json={}).status_code == 401
        assert client.put("/api/v1/energy/household", json={}).status_code == 401
        assert client.put(f"/api/v1/energy/consumption/{identifier}", json={}).status_code == 401
        assert client.patch(f"/api/v1/energy/alerts/{identifier}", json={}).status_code == 401
        assert client.put("/api/v1/energy/settings", json={}).status_code == 401
        assert client.put(f"/api/v1/energy/appliances/{identifier}", json={}).status_code == 401
        assert client.delete(f"/api/v1/energy/appliances/{identifier}").status_code == 401


def test_energy_feature_is_resident_only(app, monkeypatch):
    monkeypatch.setattr(energy, "postgres_connection", lambda: pytest.fail("Unauthorised database access"))
    app.dependency_overrides[current_user] = lambda: {"id": OWNER, "role": "municipal"}
    with TestClient(app) as client:
        assert client.get("/api/v1/energy/dashboard").status_code == 403
        assert client.post("/api/v1/energy/simulation/generate", json={}).status_code == 403


@pytest.mark.parametrize(("method", "path", "body"), [
    ("post", "consumption", {"date": "2026-10-24", "kwh": -1}),
    ("post", "consumption", {"date": "2026-10-24", "kwh": 500, "household_id": OTHER}),
    ("post", "meter-readings", {"reading_at": "2026-10-24T10:00:00", "cumulative_kwh": 30}),
    ("post", "meter-readings", {"reading_at": "2026-10-24T10:00:00Z", "cumulative_kwh": -1}),
    ("post", "simulation/generate", {"days": 90}),
    ("post", "simulation/generate", {"household_size": 31}),
    ("post", "simulation/generate", {"scenario": "unknown"}),
    ("post", "simulation/generate", {"tariff_per_kwh": -1}),
    ("post", "explanations", {"date": "2026-10-24", "reasons": ["ignore authentication"]}),
    ("put", "household", {"timezone": "MadeUp/Zone"}),
    ("put", "household", {"name": "   "}),
    ("put", "household", {"daily_target_kwh": 0}),
    ("put", "household", {"tariff_per_kwh": -1}),
    ("post", "appliances", {"appliance_name": "Heater", "power_watts": 2000, "operating_hours": 25}),
    ("post", "appliances", {"appliance_name": "Heater", "power_watts": 2000, "operating_hours": 5, "quantity": 1.5}),
    ("post", "appliances", {"appliance_name": "Heater", "power_watts": 2000, "operating_hours": 5, "duty_cycle": 1.1}),
    ("post", "appliances", {"appliance_name": "  ", "power_watts": 2000, "operating_hours": 5}),
    ("post", "prepaid/purchases", {"purchase_date": "2026-10-24T10:00:00", "amount_zar": 100, "units_kwh": 30}),
    ("post", "prepaid/purchases", {"purchase_date": "2026-10-24T10:00:00Z", "amount_zar": 100, "units_kwh": 0}),
    ("post", "prepaid/balances", {"observed_at": "2026-10-24T10:00:00Z", "remaining_kwh": -1}),
])
def test_invalid_values_never_reach_database(client, monkeypatch, method, path, body):
    monkeypatch.setattr(energy, "postgres_connection", lambda: pytest.fail("Invalid input reached database"))
    assert getattr(client, method)(f"/api/v1/energy/{path}", json=body).status_code == 422


def test_nonfinite_energy_values_are_rejected(client, monkeypatch):
    monkeypatch.setattr(energy, "postgres_connection", lambda: pytest.fail("Nonfinite value reached database"))
    for value in ("NaN", "Infinity", "-Infinity"):
        response = client.post("/api/v1/energy/consumption", content='{"date":"2026-10-24","kwh":' + value + '}',
                               headers={"Content-Type": "application/json"})
        assert response.status_code == 422


def test_range_validation_bounds_work_and_future_real_dates():
    today = date(2026, 10, 25)
    assert energy._range(today, 90, None, None) == (date(2026, 7, 28), today)
    for start, end in [(today + timedelta(days=1), today), (today - timedelta(days=90), today),
                       (today - timedelta(days=367), today - timedelta(days=350)), (today, today + timedelta(days=1))]:
        with pytest.raises(HTTPException) as exc:
            energy._range(today, 30, start, end)
        assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        energy._actual_date(date.today() + timedelta(days=2), SETTINGS)
    assert exc.value.status_code == 422


def test_database_failure_is_explicit_without_credentials(client, monkeypatch):
    def broken():
        raise RuntimeError("postgres://secret-password-should-not-leak")
    monkeypatch.setattr(energy, "postgres_connection", broken)
    response = client.get("/api/v1/energy/dashboard")
    assert response.status_code == 503
    assert "migration 015" in response.json()["detail"]
    assert "secret-password" not in response.text


def test_household_lookup_checks_owner_role_status_and_serialises_writes():
    calls = []
    class Connection:
        def execute(self, sql, params):
            calls.append((sql, params))
            return Cursor(SETTINGS)
    assert energy._household(Connection(), {"id": OWNER}, lock=True)["owner_id"] == OWNER
    assert calls[0][1] == (OWNER,)
    assert "h.owner_id=%s" in calls[0][0] and "p.role='resident'" in calls[0][0]
    assert "account_status" in calls[0][0] and "for update of h" in calls[0][0]


def test_missing_or_inactive_household_cannot_be_authorised_by_payload():
    class Connection:
        def execute(self, sql, params):
            assert params == (OWNER,)
            return Cursor()
    with pytest.raises(HTTPException) as exc:
        energy._household(Connection(), {"id": OWNER}, lock=True)
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
    form = energy.MeterInput(reading_at=timestamp, cumulative_kwh=10)
    with pytest.raises(HTTPException, match="decreased"):
        energy._validate_meter_neighbors(Connection({"cumulative_kwh": 20}, None), SETTINGS, form)
    energy._validate_meter_neighbors(Connection({"cumulative_kwh": 20}, {"cumulative_kwh": 11, "is_reset": False}),
                                    SETTINGS, form.model_copy(update={"is_reset": True}))
    with pytest.raises(HTTPException, match="above the next"):
        energy._validate_meter_neighbors(Connection({"cumulative_kwh": 9}, {"cumulative_kwh": 9.5, "is_reset": False}), SETTINGS, form)
    energy._validate_meter_neighbors(Connection({"cumulative_kwh": 9}, {"cumulative_kwh": 1, "is_reset": True}), SETTINGS, form)


def test_edit_daily_record_scopes_id_to_current_household(client, monkeypatch):
    calls = []
    class Connection(FakeConnection):
        def execute(self, sql, params=()):
            if sql.startswith("set local"):
                return Cursor()
            calls.append((sql, params))
            return Cursor()
    monkeypatch.setattr(energy, "postgres_connection", Connection)
    monkeypatch.setattr(energy, "_household", lambda *_args, **_kwargs: SETTINGS)
    response = client.put(f"/api/v1/energy/consumption/{uuid4()}", json={"date": date.today().isoformat(), "kwh": 600})
    assert response.status_code == 404
    assert "where id=%s and household_id=%s" in calls[0][0]
    assert calls[0][1][-1] == HOUSEHOLD


def test_reports_and_alerts_deduplicate_while_real_corrections_update_original():
    state = {"reports": {}, "alerts": {}, "notifications": [], "calls": []}
    class Connection:
        def execute(self, sql, params=()):
            state["calls"].append((sql, params))
            if "insert into public.energy_alerts" in sql:
                state["alerts"][params[:3]] = params[3:]
            elif "insert into public.energy_daily_reports" in sql:
                key = params[:2]
                if key not in state["reports"]:
                    state["reports"][key] = params[2]
                    return Cursor({"id": "report"})
            elif "insert into public.notifications" in sql:
                state["notifications"].append(params)
                return Cursor({"id": "notification"})
            return Cursor()
    report = {"date": "2026-10-25", "kwh": 850, "target_kwh": 600, "difference_kwh": 250,
              "status": "Above Target", "is_estimated": False, "is_simulation": False}
    alert = {"consumption_date": "2026-10-25", "alert_type": "above_target", "severity": "warning", "message": "High use"}
    dashboard = {"is_simulation": False, "daily_reports": [report], "alerts": [alert]}
    energy._persist_reports(Connection(), SETTINGS, dashboard)
    energy._persist_reports(Connection(), SETTINGS, dashboard)
    assert len(state["reports"]) == len(state["alerts"]) == len(state["notifications"]) == 1
    assert any("update public.notifications" in sql for sql, _ in state["calls"])
    assert "Open Energy Management" in state["notifications"][0][1]
    before = len(state["calls"])
    energy._persist_reports(Connection(), SETTINGS, {**dashboard, "is_simulation": True})
    assert len(state["calls"]) == before
    energy._persist_reports(Connection(), SETTINGS, {"daily_reports": [{**report, "kwh": None}], "alerts": []})
    assert len(state["notifications"]) == 1


def test_simulation_flow_is_persistent_owner_scoped_cached_and_separate(client, monkeypatch, app):
    connection = FakeConnection()
    monkeypatch.setattr(energy, "postgres_connection", lambda: connection)
    monkeypatch.setattr(energy, "_household", lambda *_args, **_kwargs: deepcopy(SETTINGS))
    monkeypatch.setattr(energy, "_today", lambda _: date(2026, 10, 25))
    analyses = []
    def advisor(evidence, explanation=None):
        analyses.append((deepcopy(evidence), deepcopy(explanation)))
        return {"source": "gemini", "model": "test-model", "generated_at": STAMP.isoformat(),
                "analysis": {"summary": "Review this illustrative increase.", "possible_causes": [],
                             "recommendations": ["Review appliance operating time."], "caveat": "Simulation cannot confirm real appliance causes."}}
    monkeypatch.setattr(energy, "analyse_energy", advisor)
    response = client.post("/api/v1/energy/simulation/generate", json={"days": 30, "scenario": "normal"})
    assert response.status_code == 201
    dashboard = response.json()
    identifier = dashboard["session_id"]
    assert len(dashboard["series"]) == 30 and dashboard["is_simulation"]
    assert client.get("/api/v1/energy/simulation").json()["session_id"] == identifier
    leaked = client.post("/api/v1/energy/simulation/trigger-spike", json={"session_id": identifier}).json()
    assert leaked["summary"]["today_kwh"] > SETTINGS["daily_target_kwh"] * 1.5
    assert any(alert["alert_type"] == "unusual_increase" for alert in leaked["alerts"])
    last = leaked["as_of_date"]
    body = {"date": last, "session_id": identifier}
    first_analysis = client.post("/api/v1/energy/ai/analyse", json=body)
    assert first_analysis.status_code == 200
    assert client.post("/api/v1/energy/ai/analyse", json=body).json()["id"] == first_analysis.json()["id"]
    assert len(analyses) == 1
    explained = client.post("/api/v1/energy/explanations", json={**body, "reasons": ["visitors", "laundry"], "text": "We had visitors."})
    assert explained.status_code == 200 and len(analyses) == 2
    assert explained.json()["explanation"]["reasons"] == ["visitors", "laundry"]
    assert analyses[-1][1]["text"] == "We had visitors."
    stored = client.get(f"/api/v1/energy/insights?date={last}&session_id={identifier}").json()
    assert stored["explanation"]["text"] == "We had visitors." and len(stored["items"]) == 1
    alert = next(row for row in leaked["alerts"] if row["alert_type"] == "unusual_increase")
    assert client.patch(f"/api/v1/energy/alerts/{alert['id']}?session_id={identifier}", json={"status": "dismissed"}).status_code == 200
    restored = client.get(f"/api/v1/energy/dashboard?session_id={identifier}").json()
    assert next(row for row in restored["alerts"] if row["id"] == alert["id"])["status"] == "dismissed"
    assert client.post("/api/v1/energy/simulation/next-day", json={"session_id": identifier}).status_code == 200
    app.dependency_overrides[current_user] = lambda: {"id": OTHER, "role": "resident"}
    assert client.get(f"/api/v1/energy/dashboard?session_id={identifier}").status_code == 404
    assert client.post("/api/v1/energy/simulation/reset", json={"session_id": identifier}).status_code == 404
    app.dependency_overrides[current_user] = lambda: {"id": OWNER, "role": "resident"}
    assert client.post("/api/v1/energy/simulation/reset", json={"session_id": identifier}).json()["reset"] is True
    assert client.get("/api/v1/energy/simulation").json() is None
    assert all("public.notifications" not in sql and "public.energy_consumption_daily" not in sql and "public.energy_meter_readings" not in sql for sql, _ in connection.calls)


def test_missing_day_analysis_and_explanation_fail_before_provider(client, monkeypatch):
    session = {"id": str(uuid4()), "scenario": "normal", "created_at": STAMP,
               "payload": {"household": SETTINGS, "records": [{"id": str(uuid4()), "date": "2026-10-25", "kwh": 500}]}}
    monkeypatch.setattr(energy, "_session", lambda *_args, **_kwargs: deepcopy(session))
    monkeypatch.setattr(energy, "postgres_connection", FakeConnection)
    monkeypatch.setattr(energy, "analyse_energy", lambda *_args: pytest.fail("Provider called without actual data"))
    body = {"date": "2026-10-24", "session_id": session["id"]}
    assert client.post("/api/v1/energy/ai/analyse", json=body).status_code == 422
    assert client.post("/api/v1/energy/explanations", json=body).status_code == 422


def test_cache_signature_uses_explanation_meaning_and_model_configuration(monkeypatch):
    evidence = {"facts": {"kwh": 850}}
    explanation = {"id": str(uuid4()), "updated_at": STAMP.isoformat(), "text": "Laundry", "reasons": ["laundry"]}
    baseline = energy._fingerprint(evidence, explanation)
    assert energy._fingerprint(evidence, {**explanation, "id": str(uuid4()), "updated_at": "later"}) == baseline
    assert energy._fingerprint(evidence, {**explanation, "text": "Visitors"}) != baseline
    assert energy._fingerprint({"facts": {"kwh": 900}}, explanation) != baseline
    monkeypatch.setenv("GEMINI_MODEL", "a-different-model")
    assert energy._fingerprint(evidence, explanation) != baseline


def test_uncached_analysis_rate_limit_is_per_resident():
    energy._analysis_attempts.clear()
    for _ in range(8):
        energy._limit_analysis(OWNER)
    with pytest.raises(HTTPException) as exc:
        energy._limit_analysis(OWNER)
    assert exc.value.status_code == 429
    energy._limit_analysis(OTHER)


def test_saved_daily_reading_commits_before_automatic_analysis_failure(client, monkeypatch):
    events = []
    class Connection:
        def __enter__(self):
            events.append("begin")
            return self
        def __exit__(self, kind, *_):
            events.append("rollback" if kind else "commit")
            return False
        def execute(self, sql, params=()):
            if "insert into public.energy_consumption_daily" in sql:
                events.append("saved")
                return Cursor({"id": str(uuid4()), "date": params[1], "kwh": params[2]})
            return Cursor()
    monkeypatch.setattr(energy, "postgres_connection", Connection)
    monkeypatch.setattr(energy, "_household", lambda *_args, **_kwargs: SETTINGS)
    monkeypatch.setattr(energy, "_today", lambda _: date(2026, 10, 25))
    monkeypatch.setattr(energy, "_actual_dashboard", lambda *_args, **_kwargs: {})
    def broken_analysis(*_args, **_kwargs):
        assert events[:3] == ["begin", "saved", "commit"]
        raise RuntimeError("provider-failed-with-sensitive-key")
    monkeypatch.setattr(energy, "_analyse", broken_analysis)
    response = client.post("/api/v1/energy/consumption", json={"date": "2026-10-25", "kwh": 22})
    assert response.status_code == 201 and response.json()["kwh"] == 22
    assert response.json()["automatic_analysis"]["status"] == "unavailable"
    assert events == ["begin", "saved", "commit", "begin", "rollback"]
    assert "sensitive-key" not in response.text


def test_simulation_appliance_crud_is_isolated_and_invalidates_cached_advice(client, monkeypatch, app):
    connection = FakeConnection()
    monkeypatch.setattr(energy, "postgres_connection", lambda: connection)
    monkeypatch.setattr(energy, "_household", lambda *_args, **_kwargs: deepcopy(SETTINGS))
    monkeypatch.setattr(energy, "_today", lambda _: date(2026, 10, 25))
    monkeypatch.setattr(energy, "analyse_energy", lambda *_args: {"source": "gemini", "model": "test",
        "generated_at": datetime.now(timezone.utc).isoformat(), "analysis": {"summary": "Review entered estimates."}})
    session = client.post("/api/v1/energy/simulation/generate", json={"days": 7}).json()
    sid = session["session_id"]
    params = {"session_id": sid}
    appliance = {"appliance_name": "Heater", "power_watts": 2000, "operating_hours": 5, "quantity": 1, "duty_cycle": 0.5}
    saved = client.post("/api/v1/energy/appliances", params=params, json=appliance)
    assert saved.status_code == 201
    record_id = saved.json()["id"]
    estimates = client.get("/api/v1/energy/appliances", params=params).json()
    assert estimates["total_daily_kwh"] == 5
    assert estimates["recommendations"][0]["energy_reduction_kwh_per_day"] == 1
    analysis = client.post("/api/v1/energy/ai/analyse", json={"date": session["as_of_date"], "session_id": sid})
    assert analysis.status_code == 200
    assert client.get("/api/v1/energy/insights", params={**params, "consumption_date": session["as_of_date"]}).json()["items"]
    assert client.put(f"/api/v1/energy/appliances/{record_id}", params=params,
                      json={**appliance, "operating_hours": 2}).status_code == 200
    assert not client.get("/api/v1/energy/insights", params={**params, "date": session["as_of_date"]}).json()["items"]
    app.dependency_overrides[current_user] = lambda: {"id": OTHER, "role": "resident"}
    assert client.get("/api/v1/energy/appliances", params=params).status_code == 404
    assert client.delete(f"/api/v1/energy/appliances/{record_id}", params=params).status_code == 404
    app.dependency_overrides[current_user] = lambda: {"id": OWNER, "role": "resident"}
    assert client.delete(f"/api/v1/energy/appliances/{record_id}", params=params).json()["deleted"]
    assert client.get("/api/v1/energy/appliances", params=params).json()["total_daily_kwh"] == 0
    assert all("public.appliances" not in sql and "public.notifications" not in sql for sql, _ in connection.calls)


def test_real_appliance_mutations_filter_verified_resident_not_supplied_owner(client, monkeypatch):
    calls = []
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def execute(self, sql, params=()):
            calls.append((sql, params))
            return Cursor()
    monkeypatch.setattr(energy, "postgres_connection", Connection)
    monkeypatch.setattr(energy, "_household", lambda *_args, **_kwargs: SETTINGS)
    identifier = str(uuid4())
    appliance = {"appliance_name": "Heater", "power_watts": 2000, "operating_hours": 5}
    assert client.put(f"/api/v1/energy/appliances/{identifier}", json=appliance).status_code == 404
    assert client.delete(f"/api/v1/energy/appliances/{identifier}").status_code == 404
    writes = [(sql, params) for sql, params in calls if "public.appliances" in sql]
    assert len(writes) == 2
    assert all("where id=%s and resident_id=%s" in sql and params[-1] == OWNER for sql, params in writes)


def test_incomplete_legacy_appliance_is_readable_without_fabricated_usage():
    result = energy._appliance_estimates([{"id": "legacy", "appliance_name": "Unknown appliance",
        "power_watts": None, "operating_hours": None}], SETTINGS, date(2026, 10, 25))
    assert result["appliances"][0]["daily_kwh"] is None
    assert result["appliances"][0]["incomplete"] is True
    assert result["recommendations"] == []


def test_prepaid_records_require_offset_and_cannot_be_future_dated():
    with pytest.raises(HTTPException) as exc:
        energy._prepaid_date(datetime.now(timezone.utc) + timedelta(days=1), SETTINGS)
    assert exc.value.status_code == 422


def test_prepaid_purchase_and_observed_balance_persist_and_are_owner_scoped(client, monkeypatch, app):
    state = {"purchases": [], "balances": [], "calls": []}
    day = date(2026, 10, 24)
    stamp = "2026-10-24T08:00:00+02:00"
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def execute(self, sql, params=()):
            state["calls"].append((sql, params))
            if sql.startswith("set local"):
                return Cursor()
            if "insert into public.energy_prepaid_purchases" in sql:
                hid, purchased_at, amount, units = params
                result = {"id": str(uuid4()), "household_id": hid, "purchase_date": purchased_at,
                          "amount_zar": amount, "units_kwh": units, "created_at": STAMP}
                state["purchases"].append(result)
                return Cursor(result)
            if "insert into public.energy_prepaid_balances" in sql:
                hid, observed_at, remaining = params
                result = {"id": str(uuid4()), "household_id": hid, "observed_at": observed_at,
                          "remaining_kwh": remaining, "created_at": STAMP}
                state["balances"].append(result)
                return Cursor(result)
            if "from public.energy_prepaid_purchases" in sql:
                return Cursor(rows=[p for p in state["purchases"] if p["household_id"] == params[0]])
            if "from public.energy_prepaid_balances" in sql:
                return Cursor(rows=[p for p in state["balances"] if p["household_id"] == params[0]])
            pytest.fail(sql)
    monkeypatch.setattr(energy, "postgres_connection", Connection)
    monkeypatch.setattr(energy, "_household", lambda _connection, user, **_kwargs: {**SETTINGS,
        "id": HOUSEHOLD if user["id"] == OWNER else OTHER, "owner_id": user["id"]})
    monkeypatch.setattr(energy, "_actual_dashboard", lambda _connection, household, **_kwargs: {
        "household": household, "as_of_date": day.isoformat(), "summary": {"seven_day_average_kwh": 15},
        "series": [{"date": day.isoformat(), "kwh": 15, "coverage_fraction": 1}]})
    purchase = client.post("/api/v1/energy/prepaid/purchases", json={"purchase_date": stamp, "amount_zar": 100, "units_kwh": 30})
    assert purchase.status_code == 201 and purchase.json()["units_kwh"] == 30
    no_balance = client.get("/api/v1/energy/prepaid").json()
    assert no_balance["summary"]["estimated_remaining_kwh"] is None
    assert "Insufficient information" in no_balance["summary"]["note"]
    balance = client.post("/api/v1/energy/prepaid/balances", json={"observed_at": stamp, "remaining_kwh": 45})
    assert balance.status_code == 201 and balance.json()["remaining_kwh"] == 45
    saved = client.get("/api/v1/energy/prepaid").json()
    assert len(saved["purchases"]) == len(saved["balances"]) == 1
    assert saved["summary"]["monthly_amount_zar"] == 100
    assert saved["summary"]["estimated_remaining_kwh"] == 45
    assert saved["summary"]["estimated_days_remaining"] == 3
    assert "observation" in saved["summary"]["note"]
    app.dependency_overrides[current_user] = lambda: {"id": OTHER, "role": "resident"}
    other = client.get("/api/v1/energy/prepaid").json()
    assert not other["purchases"] and not other["balances"]
    assert all("where household_id=%s" in sql for sql, _ in state["calls"] if sql.lstrip().startswith("select"))


def test_report_cost_requires_configured_tariff_and_preserves_actual_zero():
    report = {"date": "2026-10-25", "kwh": 0, "target_kwh": 15, "difference_kwh": -15, "status": "Normal"}
    assert "unavailable (set your electricity tariff)" in energy._report_body(report)
    assert "R0.00" not in energy._report_body(report)
    assert "R0.00" in energy._report_body({**report, "estimated_cost_rand": 0})


def test_simulation_assumed_tariff_does_not_change_unconfigured_actual_settings(client, monkeypatch):
    connection = FakeConnection()
    actual = {**SETTINGS, "tariff_per_kwh": 0}
    monkeypatch.setattr(energy, "postgres_connection", lambda: connection)
    monkeypatch.setattr(energy, "_household", lambda *_args, **_kwargs: deepcopy(actual))
    monkeypatch.setattr(energy, "_today", lambda _: date(2026, 10, 25))
    generated = client.post("/api/v1/energy/simulation/generate", json={"days": 7}).json()
    assert generated["household"]["tariff_per_kwh"] == 2.75
    assert generated["series"][-1]["estimated_cost_rand"] > 0
    assert "Assumed demo tariff" in generated["tariff_assumption"]
    configured = client.post("/api/v1/energy/simulation/generate", json={"days": 7, "tariff_per_kwh": 3.5}).json()
    assert configured["household"]["tariff_per_kwh"] == 3.5
    assert actual["tariff_per_kwh"] == 0
    assert all("public.energy_households" not in sql for sql, _ in connection.calls)


def test_simulation_profile_defaults_never_provision_an_actual_household():
    calls = []
    class Connection:
        def execute(self, sql, params=()):
            calls.append((sql, params))
            if "from public.profiles" in sql:
                return Cursor({"id": OWNER, "household_size": 3})
            return Cursor()
    household = energy._household(Connection(), {"id": OWNER}, provision=False)
    assert household["owner_id"] == OWNER and household["household_size"] == 3
    assert all(sql.lstrip().startswith("select") for sql, _ in calls)

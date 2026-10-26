"""Overview uses the same real-data aggregation as the household workspaces."""
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import energy, water, main
from backend.app.services.resident_summary import consumption_summary


class Records:
    def __init__(self, *, include_records=True):
        self.calls = []
        self.include_records = include_records

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        rows = []
        if self.include_records:
            for kind, unit, amount, target in (("water", "litres", 500, 600), ("energy", "kwh", 18, 20)):
                if f"from public.{kind}_consumption_daily " in sql:
                    rows = [{"id": f"{kind}-daily", "date": date(2026, 10, 26), unit: amount,
                             f"target_{unit}": target, "data_source": "manual", "is_simulation": False}]
                if f"from public.{kind}_readings " in sql:
                    # The legacy interval overlaps the manual daily entry. It
                    # must not be counted a second time by the overview.
                    rows = [{"id": f"{kind}-legacy", "previous_value": 10, "current_value": 12,
                             "previous_date": date(2026, 10, 25), "reading_date": date(2026, 10, 26)}]
        return SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: rows[0] if rows else None)


@pytest.fixture
def households(monkeypatch):
    for kind, module, target in (("water", water, 600), ("energy", energy, 20)):
        amount = "litres" if kind == "water" else "kwh"
        tariff = "tariff_per_kl" if kind == "water" else "tariff_per_kwh"
        settings = {"id": f"{kind}-household", "owner_id": "owner", "name": "Home",
                    "household_size": 4, f"daily_target_{amount}": target, tariff: 2,
                    "timezone": "Africa/Johannesburg"}
        monkeypatch.setattr(module, "_household", lambda con, user, settings=settings: settings)
        monkeypatch.setattr(module, "_today", lambda _: date(2026, 10, 26))


def test_new_daily_values_reach_overview_without_double_counting_legacy(households):
    connection = Records()
    result = consumption_summary(connection, {"id": "owner"})
    assert result["water"]["consumption_kilolitres"] == .5
    assert result["energy"]["consumption_kwh"] == 18
    assert result["water"]["observed_days"] == 1
    assert result["energy"]["estimated_days"] == 0
    assert result["water"]["series"][-1]["consumption"] == .5
    assert result["energy"]["series"][-1]["consumption"] == 18
    assert not any("simulation" in sql or sql.strip().startswith(("insert", "update")) for sql, _ in connection.calls)
    assert all(params[0] == "owner" for sql, params in connection.calls if "from public.water_readings " in sql or "from public.energy_readings " in sql)


def test_missing_days_stay_missing(households):
    result = consumption_summary(Records(include_records=False), {"id": "owner"})
    assert result["water"]["consumption_kilolitres"] is None
    assert result["energy"]["consumption_kwh"] is None
    assert result["water"]["reading_count"] == 0
    assert all(point["consumption"] is None for point in result["energy"]["series"])


def test_appliance_delete_preflight_accepts_trusted_origin():
    response = TestClient(main.app).options("/api/v1/energy/appliances/test", headers={
        "Origin": main.origins[0], "Access-Control-Request-Method": "DELETE",
        "Access-Control-Request-Headers": "authorization,x-csrf-token",
    })
    assert response.status_code == 200
    assert "DELETE" in response.headers["access-control-allow-methods"]

"""Exercise reverse-location fallbacks through the public API without providers."""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import workspace
from backend.app.main import app


JOHANNESBURG = {
    "id": "johannesburg-id", "name": "City of Johannesburg",
    "province_id": "gauteng-id", "province": "Gauteng",
}
OFFICIAL_AREA = {
    "name": "City of Johannesburg Metropolitan Municipality", "province": "Gauteng",
    "source": "Municipal Demarcation Board 2026",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.app.main.DB_PATH", tmp_path / "location.sqlite3")
    monkeypatch.setenv("ECOPULSE_LOCAL_DEMO", "true")
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize("failure", [httpx.ConnectError("offline"), asyncio.TimeoutError()])
def test_official_boundary_resolves_canonical_ids_when_place_names_fail(client, monkeypatch, failure):
    async def reverse(*args):
        raise failure

    async def boundary(latitude, longitude):
        assert (latitude, longitude) == (-26.2485, 27.854)
        return dict(OFFICIAL_AREA)

    def execute(sql, params=(), **kwargs):
        if "st_covers" in sql:
            return []
        assert "m.is_demonstration is false" in sql
        assert "m.municipality_type in ('local','metropolitan')" in sql
        assert params == ("Gauteng",)
        return [dict(JOHANNESBURG)]

    monkeypatch.setattr(workspace.weather_service, "reverse_location", reverse)
    monkeypatch.setattr(workspace.municipality_boundaries, "municipality_at_point", boundary)
    monkeypatch.setattr(workspace, "_execute", execute)
    response = client.get("/api/v1/locations/reverse", params={"latitude": -26.2485, "longitude": 27.854})
    assert response.status_code == 200
    area = response.json()
    assert area["municipality"] == JOHANNESBURG
    assert area["province"] == {"id": "gauteng-id", "name": "Gauteng"}
    assert area["locality"] is None  # A municipality polygon cannot identify a suburb.
    assert area["source"] == OFFICIAL_AREA["source"]
    assert area["boundary_source"] == OFFICIAL_AREA["source"]
    assert area["country_code"] == "za"


def test_database_boundary_keeps_locality_and_skips_external_fallback(client, monkeypatch):
    async def reverse(*args):
        return {"label": "Vilakazi Street, Orlando West, Gauteng", "locality": "Orlando West",
                "country_code": "ZA", "source": "OpenStreetMap Nominatim"}

    async def boundary(*args):
        pytest.fail("An existing database boundary must not call the external fallback")

    monkeypatch.setattr(workspace.weather_service, "reverse_location", reverse)
    monkeypatch.setattr(workspace.municipality_boundaries, "municipality_at_point", boundary)
    monkeypatch.setattr(workspace, "_execute", lambda *args, **kwargs: [dict(JOHANNESBURG)])
    area = client.get("/api/v1/locations/reverse?latitude=-26.2485&longitude=27.854").json()
    assert area["municipality"] == JOHANNESBURG
    assert area["locality"] == "Orlando West"
    assert area["source"] == "OpenStreetMap Nominatim"
    assert area["boundary_source"] == "Municipality boundary data"


@pytest.mark.parametrize("rows", [[], [JOHANNESBURG, {**JOHANNESBURG, "id": "duplicate-id"}]])
def test_official_name_must_map_to_one_canonical_municipality(client, monkeypatch, rows):
    async def reverse(*args):
        return {}

    async def boundary(*args):
        return dict(OFFICIAL_AREA)

    def execute(sql, params=(), *, fetch="all"):
        if fetch == "one":
            return {"id": "gauteng-id", "name": "Gauteng"}
        return [] if "st_covers" in sql else rows

    monkeypatch.setattr(workspace.weather_service, "reverse_location", reverse)
    monkeypatch.setattr(workspace.municipality_boundaries, "municipality_at_point", boundary)
    monkeypatch.setattr(workspace, "_execute", execute)
    area = client.get("/api/v1/locations/reverse?latitude=-26.2485&longitude=27.854").json()
    assert area["municipality"] is None
    assert area["province"] == {"id": "gauteng-id", "name": "Gauteng"}


@pytest.mark.parametrize("provider_error", [False, True])
def test_unavailable_or_ambiguous_boundaries_allow_manual_selection(client, monkeypatch, provider_error):
    async def reverse(*args):
        raise httpx.ConnectError("place names unavailable")

    async def boundary(*args):
        if provider_error:
            raise ValueError("boundary provider unavailable")
        return None

    monkeypatch.setattr(workspace.weather_service, "reverse_location", reverse)
    monkeypatch.setattr(workspace.municipality_boundaries, "municipality_at_point", boundary)
    monkeypatch.setattr(workspace, "_execute", lambda *args, **kwargs: [])
    response = client.get("/api/v1/locations/reverse?latitude=-26.2485&longitude=27.854")
    assert response.status_code == 200
    area = response.json()
    assert area["province"] is None
    assert area["municipality"] is None
    assert area["source"] is None
    assert area["country_code"] is None  # An outage is not evidence of a foreign location.


def test_foreign_country_is_explicit_and_skips_south_african_lookups(client, monkeypatch):
    async def reverse(*args):
        return {"label": "London", "locality": "London", "province": "Gauteng",
                "country_code": "gb", "source": "OpenStreetMap Nominatim"}

    async def boundary(*args):
        pytest.fail("Foreign points must not use South African boundaries")

    def execute(*args, **kwargs):
        pytest.fail("Foreign points must not match South African province names")

    monkeypatch.setattr(workspace.weather_service, "reverse_location", reverse)
    monkeypatch.setattr(workspace.municipality_boundaries, "municipality_at_point", boundary)
    monkeypatch.setattr(workspace, "_execute", execute)
    area = client.get("/api/v1/locations/reverse?latitude=51.5&longitude=-0.12").json()
    assert area["country_code"] == "gb"
    assert area["province"] is None
    assert area["municipality"] is None


@pytest.mark.parametrize("latitude,longitude", [(91, 28), (-26, 181), ("nan", 28)])
def test_invalid_location_is_rejected_before_provider_calls(client, latitude, longitude):
    assert client.get("/api/v1/locations/reverse", params={"latitude": latitude, "longitude": longitude}).status_code == 422

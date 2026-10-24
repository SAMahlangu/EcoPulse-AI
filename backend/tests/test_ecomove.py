import logging
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import ecomove
from backend.app.security import current_user
from backend.app.services.ecomove_matching import match_journeys

MUNICIPALITY = "8a4889eb-fbde-45d9-b7e1-e5752fb64f00"
ROUTE_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(ecomove.router)
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(app):
    app.dependency_overrides[current_user] = lambda: {"id": "resident", "role": "resident"}
    with TestClient(app) as client:
        yield client


def stop(identifier, longitude, sequence, *, stop_type="rank", interchange=False, offset=None):
    return {"id": identifier, "name": identifier, "municipality_id": MUNICIPALITY,
            "latitude": -25.75, "longitude": longitude, "stop_type": stop_type,
            "is_demo": True, "is_interchange": interchange, "stop_sequence": sequence,
            "estimated_arrival_offset_minutes": offset}


def route(identifier, stops, **overrides):
    return {"id": identifier, "name": f"Demo route {identifier}", "origin": "Demo origin",
            "destination": "Demo destination", "municipality_id": MUNICIPALITY,
            "route_geometry": {"type": "LineString", "coordinates": [[28, -25.75], [28.03, -25.75]]},
            "direction": "forward", "description": "Demonstration only", "color": "#0d9488",
            "estimated_duration_minutes": None, "estimated_fare": None, "is_demo": True,
            "data_source": "demo", "simulation_mode": "once", "stops": stops, **overrides}


def coord(longitude):
    return {"latitude": -25.75, "longitude": longitude}


def test_every_endpoint_requires_existing_authentication(app):
    with TestClient(app) as client:
        for endpoint in ["routes", f"routes/{ROUTE_ID}", "stops", "stops/nearby?latitude=-25&longitude=28",
                         "routes/nearby?latitude=-25&longitude=28", "simulation/vehicles", "disruptions"]:
            assert client.get(f"/api/v1/ecomove/{endpoint}").status_code == 401
        assert client.post("/api/v1/ecomove/journey/search", json={
            "pickup": coord(28), "destination": coord(28.03)}).status_code == 401


@pytest.mark.parametrize("path", [
    "routes/invalid", "routes?municipality_id=invalid", "routes?limit=101", "stops?offset=-1",
    "routes?bbox=28,-26,27,-25", "stops?bbox=NaN,-26,29,-25", "routes?bbox=28,-26,29",
    "routes?bbox=28,-91,29,-25", "stops/nearby?latitude=91&longitude=28",
    "routes/nearby?latitude=-25&longitude=181", "routes/nearby?latitude=-25&longitude=28&radius_meters=10001",
    "stops/nearby?latitude=nan&longitude=28", "disruptions?radius_meters=1001",
    "disruptions?route_ids=invalid", "simulation/vehicles?route_id=invalid",
])
def test_invalid_query_inputs_never_reach_database(client, monkeypatch, path):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid input reached the database")
    monkeypatch.setattr(ecomove, "_execute", unexpected)
    assert client.get(f"/api/v1/ecomove/{path}").status_code == 422


def test_journey_validates_coordinate_radius_and_unknown_fields(client, monkeypatch):
    monkeypatch.setattr(ecomove, "_execute", lambda *args, **kwargs: pytest.fail("Unexpected database access"))
    for body in [
        {"pickup": coord(181), "destination": coord(28)},
        {"pickup": coord(28), "destination": coord(28), "radius_meters": 10001},
        {"pickup": coord(28), "destination": coord(28), "allow_unverified": True},
    ]:
        assert client.post("/api/v1/ecomove/journey/search", json=body).status_code == 422


def test_route_list_bounds_paginates_and_keeps_stop_order(client, monkeypatch):
    calls = []
    data = route(ROUTE_ID, [])
    def execute(sql, params=(), *, fetch="all"):
        calls.append((sql, params, fetch))
        if fetch == "one":
            return {"total": 25}
        if "from public.taxi_route_stops rs" in sql:
            return [{"route_id": ROUTE_ID, **stop("first", 28, 0)},
                    {"route_id": ROUTE_ID, **stop("second", 28.01, 1)}]
        return [data]
    monkeypatch.setattr(ecomove, "_execute", execute)
    result = client.get(f"/api/v1/ecomove/routes?municipality_id={MUNICIPALITY}&bbox=27,-26,29,-25&limit=5&offset=10")
    assert result.status_code == 200
    assert result.json()["total"] == 25
    assert result.json()["limit"] == 5 and result.json()["offset"] == 10
    assert [s["id"] for s in result.json()["items"][0]["stops"]] == ["first", "second"]
    assert calls[0][1] == (UUID(MUNICIPALITY), 27, -26, 29, -25)
    assert calls[1][1][-2:] == (5, 10)
    assert "st_intersects" in calls[0][0]
    assert "order by rs.route_id,rs.stop_sequence" in calls[2][0]


def test_unknown_route_is_404(client, monkeypatch):
    monkeypatch.setattr(ecomove, "_execute", lambda *args, **kwargs: [])
    assert client.get(f"/api/v1/ecomove/routes/{ROUTE_ID}").status_code == 404


def test_database_outage_is_explicit_and_redacts_exception(client, monkeypatch):
    def unavailable():
        raise RuntimeError("postgresql://secret-password-do-not-leak")
    monkeypatch.setattr(ecomove, "postgres_connection", unavailable)
    response = client.get("/api/v1/ecomove/routes")
    assert response.status_code == 503
    assert "migration 010" in response.json()["detail"]
    assert "secret-password" not in response.text


def test_nearby_routes_only_offer_configured_boarding_stops(client, monkeypatch):
    calls = []
    pickup = stop("boarding", 28, 0, stop_type="pickup")
    def execute(sql, params=(), *, fetch="all"):
        calls.append((sql, params))
        if fetch == "one":
            return {"total": 1}
        if "order by rs.route_id,rs.stop_sequence" in sql:
            return [{"route_id": ROUTE_ID, **pickup}]
        return [{**route(ROUTE_ID, []), "nearest_pickup_stop": pickup,
                 "walking_distance_meters": 23, "distance_meters": 18, "simulated_vehicle_count": 3}]
    monkeypatch.setattr(ecomove, "_execute", execute)
    response = client.get("/api/v1/ecomove/routes/nearby?latitude=-25.75&longitude=28&radius_meters=500")
    assert response.status_code == 200
    assert response.json()["items"][0]["nearest_pickup_stop"]["id"] == "boarding"
    assert "s.stop_type in ('rank','pickup','interchange')" in calls[0][0]
    assert "onward.stop_sequence>rs.stop_sequence" in calls[0][0]
    assert calls[0][1] == (28, -25.75, 500)


def test_direct_journey_respects_forward_sequence_and_configured_stop_types():
    stops = [stop("start", 28, 0, offset=0), stop("middle", 28.01, 1, offset=8),
             stop("end", 28.02, 2, offset=19)]
    data = [route("direct", stops)]
    result = match_journeys(data, coord(28), coord(28.02), 100)
    assert len(result) == 1 and result[0]["transfers"] == 0
    assert result[0]["estimated_duration_minutes"] == 19
    assert result[0]["fare"] is None
    assert match_journeys(data, coord(28.02), coord(28), 100) == []
    stops[0]["stop_type"] = "dropoff"
    assert match_journeys(data, coord(28), coord(28.02), 100) == []
    stops[0]["stop_type"], stops[-1]["stop_type"] = "pickup", "pickup"
    assert match_journeys(data, coord(28), coord(28.02), 100) == []


def test_route_geometry_proximity_never_implies_boarding():
    data = [route("route", [stop("first", 28, 0), stop("last", 28.04, 1)])]
    assert match_journeys(data, coord(28.02), coord(28.04), 100) == []


def transfer_data():
    return [route("first", [stop("start", 28, 0, offset=0),
                            stop("shared", 28.01, 1, stop_type="interchange", interchange=True, offset=8)]),
            route("second", [stop("shared", 28.01, 0, stop_type="interchange", interchange=True, offset=0),
                             stop("end", 28.02, 1, offset=7)])]


def test_single_transfer_requires_explicit_shared_interchange_and_forward_legs():
    data = transfer_data()
    results = match_journeys(data, coord(28), coord(28.02), 100)
    assert len(results) == 1 and results[0]["transfers"] == 1
    assert results[0]["estimated_duration_minutes"] == 15
    assert [leg["route_id"] for leg in results[0]["legs"]] == ["first", "second"]
    assert match_journeys(data, coord(28.02), coord(28), 100) == []
    data[1]["stops"][0]["id"] = "different-same-coordinate"
    assert match_journeys(data, coord(28), coord(28.02), 100) == []
    data = transfer_data()
    data[0]["stops"][1]["is_interchange"] = False
    assert match_journeys(data, coord(28), coord(28.02), 100) == []
    data = transfer_data()
    data[1]["stops"][0]["stop_sequence"] = 3
    assert match_journeys(data, coord(28), coord(28.02), 100) == []


def test_direct_results_take_precedence_over_transfers_and_unknown_time_is_not_invented():
    data = transfer_data()
    data.append(route("direct", [stop("start", 28, 0), stop("end", 28.02, 1)]))
    result = match_journeys(data, coord(28), coord(28.02), 100)
    assert [row["route_id"] for row in result] == ["direct"]
    assert result[0]["estimated_duration_minutes"] is None


def test_result_sorting_uses_walking_distance_before_time():
    close = route("close", [stop("start", 28, 0, offset=0), stop("end", 28.02, 1, offset=30)])
    far = route("far", [stop("start-far", 28.0003, 0, offset=0), stop("end-far", 28.0203, 1, offset=5)])
    result = match_journeys([far, close], coord(28), coord(28.02), 100)
    assert [row["route_id"] for row in result] == ["close", "far"]


def test_journey_endpoint_loads_candidates_and_returns_safe_stop_fields(client, monkeypatch):
    data = route(ROUTE_ID, [])
    monkeypatch.setattr(ecomove, "_execute", lambda *args, **kwargs: [data])
    full = route(ROUTE_ID, [stop("start", 28, 0), stop("end", 28.02, 1)])
    full["stops"][0]["private_note"] = "hidden"
    monkeypatch.setattr(ecomove, "_with_stops", lambda _: [full])
    response = client.post("/api/v1/ecomove/journey/search", json={
        "pickup": coord(28), "destination": coord(28.02), "municipality_id": MUNICIPALITY})
    assert response.status_code == 200
    assert response.json()["routes"][0]["pickup_stop"]["id"] == "start"
    assert "private_note" not in response.text
    assert "stop_sequence" not in response.json()["routes"][0]["pickup_stop"]


def test_very_dense_journey_is_explicitly_rejected(client, monkeypatch):
    monkeypatch.setattr(ecomove, "_execute", lambda *args, **kwargs: [{}] * 201)
    response = client.post("/api/v1/ecomove/journey/search", json={"pickup": coord(28), "destination": coord(28.02)})
    assert response.status_code == 422
    assert "smaller search radius" in response.json()["detail"]


def test_simulations_are_configuration_only_and_filtered(client, monkeypatch):
    calls = []
    def execute(sql, params=(), *, fetch="all"):
        calls.append((sql, params))
        if fetch == "one":
            return {"total": 1}
        return [{"id": "vehicle", "route_id": ROUTE_ID, "vehicle_code": "SIM-001",
                 "simulation_speed": 35, "initial_progress": 0.25, "is_active": True,
                 "position_source": "simulation", "latitude": -25.75}]
    monkeypatch.setattr(ecomove, "_execute", execute)
    result = client.get(f"/api/v1/ecomove/simulation/vehicles?route_id={ROUTE_ID}&municipality_id={MUNICIPALITY}")
    assert result.status_code == 200
    assert result.json()["items"][0]["position_source"] == "simulation"
    assert "latitude" not in result.json()["items"][0]
    assert "v.is_active" in calls[0][0] and "v.route_id=%s" in calls[0][0]


def test_disruption_response_allowlist_hides_private_data_and_is_route_bounded(client, monkeypatch):
    calls = []
    def execute(sql, params=(), *, fetch="all"):
        calls.append((sql, params))
        if fetch == "one":
            return {"total": 1}
        return [{"id": "incident", "category": "roads", "title": "Reported pothole",
                 "location_text": "Demo road", "latitude": -25.75, "longitude": 28,
                 "created_at": "2026-10-23T08:00:00Z", "status": "submitted", "route_ids": [ROUTE_ID],
                 "distance_meters": 15, "reporter_id": "private", "description": "private",
                 "storage_path": "private", "is_closed": True}]
    monkeypatch.setattr(ecomove, "_execute", execute)
    result = client.get(f"/api/v1/ecomove/disruptions?route_ids={ROUTE_ID}&limit=5")
    assert result.status_code == 200
    row = result.json()["items"][0]
    assert row["route_ids"] == [ROUTE_ID] and row["distance_meters"] == 15
    assert not {"reporter_id", "description", "storage_path", "is_closed"}.intersection(row)
    assert "i.status not in ('resolved','closed','duplicate','rejected','unable_to_verify')" in calls[0][0]
    assert "st_dwithin(i.location,r.route_geometry,%s)" in calls[0][0]
    assert "i.municipality_id=r.municipality_id" in calls[0][0]
    assert "r.id,r.municipality_id,r.route_geometry" in calls[0][0]
    assert "reporter_id" not in calls[0][0] and "i.description" not in calls[0][0]
    assert calls[0][1] == ([ROUTE_ID], 200)


def access_record(path):
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                             '%s - "%s %s HTTP/%s" %d',
                             ("127.0.0.1:1234", "GET", path, "1.1", 200), None)


def test_ecomove_access_logs_retain_status_but_strip_precise_location_queries():
    record = access_record("/api/v1/ecomove/routes/nearby?latitude=-25.754321&longitude=28.123456&radius_meters=1000")
    assert ecomove.EcoMoveAccessLogFilter().filter(record) is True
    assert record.getMessage() == '127.0.0.1:1234 - "GET /api/v1/ecomove/routes/nearby HTTP/1.1" 200'
    assert "25.754321" not in record.getMessage() and "28.123456" not in record.getMessage()
    assert record.args[-1] == 200


@pytest.mark.parametrize("path", ["/api/v1/locations/reverse?latitude=-25&longitude=28",
                                  "/api/v1/ecomove-other/routes?latitude=-25",
                                  "/health", "/api/v1/ecomove/routes"])
def test_location_log_filter_preserves_unrelated_paths_and_usable_plain_paths(path):
    record = access_record(path)
    original = record.getMessage()
    assert ecomove.EcoMoveAccessLogFilter().filter(record) is True
    assert record.getMessage() == original


def test_location_log_filter_preserves_non_uvicorn_records_and_installs_once():
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                              "Server diagnostic: %s", ("healthy",), None)
    assert ecomove.EcoMoveAccessLogFilter().filter(record) is True
    assert record.getMessage() == "Server diagnostic: healthy"
    ecomove._install_access_log_filter()
    ecomove._install_access_log_filter()
    installed = [item for item in logging.getLogger("uvicorn.access").filters
                 if getattr(item, "_ecopulse_ecomove_redaction", False)]
    assert len(installed) == 1

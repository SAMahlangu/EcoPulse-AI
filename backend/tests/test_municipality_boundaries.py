import asyncio
from types import SimpleNamespace

import httpx
import pytest

from backend.app.services import municipality_boundaries as boundaries


@pytest.fixture(autouse=True)
def isolated_lookups(monkeypatch):
    monkeypatch.setattr(boundaries, "_cache", {})
    monkeypatch.setattr(boundaries, "_lookups", {})


def feature(name="City of Tshwane", province="Gauteng", category="A"):
    return {"attributes": {
        "MUNICNAME": name, "PROVINCE": province,
        "CATEGORY": category, "NAMECODE": "TSH",
    }}


def mock_provider(monkeypatch, handler):
    monkeypatch.setattr(
        boundaries.network, "AsyncDNSFallbackTransport",
        lambda: httpx.MockTransport(handler),
    )


def test_official_lookup_sends_rounded_point_and_queries_only_attributes(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"features": [feature()]})

    mock_provider(monkeypatch, respond)
    result = asyncio.run(boundaries.municipality_at_point(-25.546291, 28.094481))

    assert result == {
        "name": "City of Tshwane", "province": "Gauteng",
        "source": "Municipal Demarcation Board 2026",
    }
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url).split("?")[0] == boundaries.MDB_QUERY_URL
    assert request.url.params["geometry"] == "28.0945,-25.5463"
    assert request.url.params["geometryType"] == "esriGeometryPoint"
    assert request.url.params["inSR"] == "4326"
    assert request.url.params["spatialRel"] == "esriSpatialRelIntersects"
    assert request.url.params["returnGeometry"] == "false"
    assert {"MUNICNAME", "PROVINCE", "CATEGORY"} <= set(request.url.params["outFields"].split(","))
    assert "EcoPulse" in request.headers["User-Agent"]
    assert "-25.546291" not in str(request.url)


@pytest.mark.parametrize("category", ["A", "B", " b "])
def test_unique_metro_or_local_match_excludes_districts(monkeypatch, category):
    mock_provider(monkeypatch, lambda request: httpx.Response(200, json={
        "features": [feature("District", category="C"), feature(category=category)],
    }))

    result = asyncio.run(boundaries.municipality_at_point(-25.5, 28.1))

    assert result["name"] == "City of Tshwane"


def test_duplicate_features_for_the_same_municipality_are_one_match(monkeypatch):
    mock_provider(monkeypatch, lambda request: httpx.Response(200, json={
        "features": [feature(), feature(" CITY OF TSHWANE ", " gauteng ")],
    }))

    result = asyncio.run(boundaries.municipality_at_point(-25.5, 28.1))

    assert result["name"].casefold() == "city of tshwane"
    assert result["province"].casefold() == "gauteng"


@pytest.mark.parametrize("features", [
    [],
    [feature("District", category="C")],
    [feature(), feature("City of Johannesburg")],
    [feature("", "Gauteng"), feature("City of Tshwane", "")],
])
def test_no_unique_municipality_returns_none_and_is_not_cached(monkeypatch, features):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"features": features})

    mock_provider(monkeypatch, respond)

    async def look_up_twice():
        assert await boundaries.municipality_at_point(-25.5, 28.1) is None
        assert await boundaries.municipality_at_point(-25.5, 28.1) is None

    asyncio.run(look_up_twice())

    assert len(requests) == 2
    assert not boundaries._cache
    assert not boundaries._lookups


@pytest.mark.parametrize("bad_payload", [
    [],
    {},
    {"features": None},
    {"features": "invalid"},
    {"features": [None]},
    {"features": [{"attributes": ["invalid"]}]},
])
def test_malformed_payload_is_rejected_without_caching(monkeypatch, bad_payload):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=(bad_payload if len(requests) == 1 else {"features": [feature()]}))

    mock_provider(monkeypatch, respond)

    async def retry_after_bad_response():
        with pytest.raises(ValueError):
            await boundaries.municipality_at_point(-25.5, 28.1)
        assert not boundaries._cache
        assert not boundaries._lookups
        assert (await boundaries.municipality_at_point(-25.5, 28.1))["name"] == "City of Tshwane"

    asyncio.run(retry_after_bad_response())
    assert len(requests) == 2


@pytest.mark.parametrize("failure", ["arcgis", "http", "invalid-json", "connection"])
def test_provider_failures_are_not_cached_and_later_lookup_recovers(monkeypatch, failure):
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) != 1:
            return httpx.Response(200, json={"features": [feature()]})
        if failure == "arcgis":
            return httpx.Response(200, json={"error": {"code": 503, "message": "Unavailable"}})
        if failure == "http":
            return httpx.Response(503, json={"error": "Unavailable"})
        if failure == "invalid-json":
            return httpx.Response(200, content=b"{broken")
        raise httpx.ConnectError("Unavailable", request=request)

    mock_provider(monkeypatch, respond)

    async def recover():
        with pytest.raises((ValueError, httpx.HTTPError)):
            await boundaries.municipality_at_point(-25.5, 28.1)
        assert not boundaries._cache
        assert not boundaries._lookups
        assert (await boundaries.municipality_at_point(-25.5, 28.1))["name"] == "City of Tshwane"

    asyncio.run(recover())
    assert len(requests) == 2


def test_success_cache_lasts_24_hours_and_returns_independent_copies(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(boundaries, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"features": [feature()]})

    mock_provider(monkeypatch, respond)

    async def exercise_cache():
        first = await boundaries.municipality_at_point(-25.546291, 28.094481)
        first["name"] = "Caller mutation"
        clock[0] += 86_399
        cached = await boundaries.municipality_at_point(-25.546289, 28.094479)
        assert cached["name"] == "City of Tshwane"
        cached["province"] = "Caller mutation"
        assert len(requests) == 1
        clock[0] += 1
        renewed = await boundaries.municipality_at_point(-25.546291, 28.094481)
        assert renewed["province"] == "Gauteng"
        assert len(requests) == 2

    asyncio.run(exercise_cache())


def test_concurrent_lookups_share_request_and_return_independent_results(monkeypatch):
    requests = []

    async def exercise_concurrency():
        started, release = asyncio.Event(), asyncio.Event()

        async def respond(request):
            requests.append(request)
            started.set()
            await release.wait()
            return httpx.Response(200, json={"features": [feature()]})

        mock_provider(monkeypatch, respond)
        first = asyncio.create_task(boundaries.municipality_at_point(-25.546291, 28.094481))
        await started.wait()
        second = asyncio.create_task(boundaries.municipality_at_point(-25.546289, 28.094479))
        await asyncio.sleep(0)
        release.set()
        first_result, second_result = await asyncio.gather(first, second)
        assert len(requests) == 1
        first_result["name"] = "Caller mutation"
        assert second_result["name"] == "City of Tshwane"
        assert not boundaries._lookups

    asyncio.run(exercise_concurrency())


def test_cancelling_one_waiter_keeps_shared_lookup_for_other_waiter(monkeypatch):
    requests = []

    async def exercise_cancellation():
        started, release = asyncio.Event(), asyncio.Event()

        async def respond(request):
            requests.append(request)
            started.set()
            await release.wait()
            return httpx.Response(200, json={"features": [feature()]})

        mock_provider(monkeypatch, respond)
        first = asyncio.create_task(boundaries.municipality_at_point(-25.5, 28.1))
        await started.wait()
        second = asyncio.create_task(boundaries.municipality_at_point(-25.5, 28.1))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        result = await second
        assert result["name"] == "City of Tshwane"
        assert (await boundaries.municipality_at_point(-25.5, 28.1)) == result
        assert len(requests) == 1
        assert not boundaries._lookups

    asyncio.run(exercise_cancellation())

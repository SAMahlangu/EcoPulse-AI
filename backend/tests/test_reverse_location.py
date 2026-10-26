import asyncio
import gc
from types import SimpleNamespace

import pytest

from backend.app.services import weather_service


@pytest.fixture
def reverse_state(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(weather_service, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(weather_service, "_reverse_cache", {})
    monkeypatch.setattr(weather_service, "_reverse_request_tasks", {})
    monkeypatch.setattr(weather_service, "_request_tasks", {})
    monkeypatch.setattr(weather_service, "_reverse_request_lock", asyncio.Lock())
    monkeypatch.setattr(weather_service, "_last_reverse_request", float("-inf"))
    return clock


def address():
    return {"address": {
        "road": "Church Street", "suburb": "Hatfield", "state": "Gauteng",
        "municipality": "City of Tshwane", "country_code": "za",
    }}


def test_cancelled_caller_returns_promptly_and_provider_keeps_pacing_lock(monkeypatch, reverse_state):
    calls = []

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def provider(url, query, ttl, key):
            calls.append((url, query))
            if len(calls) == 1:
                started.set()
                await release.wait()
            return address()

        monkeypatch.setattr(weather_service, "_request_uncached", provider)
        first = asyncio.create_task(weather_service.reverse_location(-25.546291, 28.094481))
        await started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(first, timeout=0.2)

        same_point = asyncio.create_task(weather_service.reverse_location(-25.546289, 28.094479))
        other_point = asyncio.create_task(weather_service.reverse_location(-26.2, 28.0))
        # Let both lookups and their nested provider tasks run, if they can.
        for _ in range(4):
            await asyncio.sleep(0)
        assert len(calls) == 1
        assert not same_point.done()
        assert not other_point.done()

        reverse_state[0] += 2
        release.set()
        same, other = await asyncio.wait_for(asyncio.gather(same_point, other_point), timeout=1)
        assert same["locality"] == other["locality"] == "Hatfield"
        assert len(calls) == 2
        assert calls[0][1]["lat"] == -25.5463
        assert calls[0][1]["lon"] == 28.0945
        assert calls[1][1]["lat"] == -26.2
        assert calls[0][0] == weather_service.REVERSE_GEOCODING_URL
        assert not weather_service._reverse_request_tasks

    asyncio.run(scenario())


def test_same_point_is_deduplicated_and_success_is_cached_for_24_hours(monkeypatch, reverse_state):
    calls = []

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def provider(url, query, ttl, key):
            calls.append(query)
            started.set()
            await release.wait()
            return address()

        monkeypatch.setattr(weather_service, "_request_uncached", provider)
        first = asyncio.create_task(weather_service.reverse_location(-25.546291, 28.094481))
        await started.wait()
        second = asyncio.create_task(weather_service.reverse_location(-25.546289, 28.094479))
        await asyncio.sleep(0)
        release.set()
        first_result, second_result = await asyncio.gather(first, second)
        assert len(calls) == 1
        assert first_result["label"] == "Church Street, Hatfield, Gauteng"
        first_result["boundary_source"] = "Caller-added boundary attribution"
        assert "boundary_source" not in second_result

        reverse_state[0] += 86_399
        cached = await weather_service.reverse_location(-25.546291, 28.094481)
        assert "boundary_source" not in cached
        assert len(calls) == 1
        reverse_state[0] += 1
        assert (await weather_service.reverse_location(-25.546291, 28.094481))["locality"] == "Hatfield"
        assert len(calls) == 2

    asyncio.run(scenario())


def test_reverse_geocoding_preserves_one_second_request_spacing(monkeypatch, reverse_state):
    waits, calls = [], []
    reverse_state[0] = 100.25
    monkeypatch.setattr(weather_service, "_last_reverse_request", 100.0)

    async def sleep(delay):
        waits.append(delay)
        reverse_state[0] += delay

    monkeypatch.setattr(weather_service, "asyncio", SimpleNamespace(
        create_task=asyncio.create_task, shield=asyncio.shield, sleep=sleep,
    ))

    async def provider(url, query, ttl, key):
        calls.append(reverse_state[0])
        return address()

    monkeypatch.setattr(weather_service, "_request_uncached", provider)
    assert asyncio.run(weather_service.reverse_location(-25.5, 28.1))["locality"] == "Hatfield"
    assert waits == [0.75]
    assert calls == [101.0]


def test_detached_failure_is_consumed_and_a_later_call_can_recover(monkeypatch, reverse_state):
    calls = []

    async def scenario():
        started, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        unhandled = []
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _loop, context: unhandled.append(context))

        async def provider(url, query, ttl, key):
            calls.append(query)
            if len(calls) == 1:
                started.set()
                await release.wait()
                completed.set()
                raise ValueError("Reverse provider unavailable")
            return address()

        monkeypatch.setattr(weather_service, "_request_uncached", provider)
        caller = asyncio.create_task(weather_service.reverse_location(-25.5, 28.1))
        await started.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        release.set()
        await completed.wait()
        for _ in range(6):
            await asyncio.sleep(0)
        gc.collect()
        assert not weather_service._reverse_cache
        assert not weather_service._reverse_request_tasks
        assert not unhandled

        reverse_state[0] += 2
        result = await weather_service.reverse_location(-25.5, 28.1)
        assert result["locality"] == "Hatfield"
        assert len(calls) == 2
        assert not unhandled

    asyncio.run(scenario())

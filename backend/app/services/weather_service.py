"""Open-Meteo adapter with bounded response caching and consistent units."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from . import network

FORECAST_TTL = int(os.getenv("WEATHER_FORECAST_CACHE_SECONDS", "1800"))
CURRENT_TTL = int(os.getenv("WEATHER_CURRENT_CACHE_SECONDS", "900"))
HISTORY_TTL = int(os.getenv("WEATHER_HISTORY_CACHE_SECONDS", "86400"))
FORECAST_URL = "https://customer-api.open-meteo.com/v1/forecast" if os.getenv("OPEN_METEO_API_KEY", "").strip() else "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://customer-api.open-meteo.com/v1/archive" if os.getenv("OPEN_METEO_API_KEY", "").strip() else "https://archive-api.open-meteo.com/v1/archive"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
MET_FORECAST_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
REVERSE_GEOCODING_URL = "https://nominatim.openstreetmap.org/reverse"
API_KEY = os.getenv("OPEN_METEO_API_KEY", "").strip()
_cache: dict[str, tuple[float, Any]] = {}
_request_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
_reverse_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_reverse_request_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
_reverse_request_lock = asyncio.Lock()
_last_reverse_request = 0.0
log = logging.getLogger(__name__)


async def _request(url: str, params: dict[str, Any], ttl: int) -> dict[str, Any]:
    query = dict(params)
    if API_KEY and "customer-api.open-meteo.com" in url:
        query["apikey"] = API_KEY
    key = url + "?" + "&".join(f"{k}={query[k]}" for k in sorted(query))
    cached = _cache.get(key)
    if ttl > 0 and cached and time.monotonic() - cached[0] < ttl:
        return cached[1]
    task = _request_tasks.get(key)
    if task is None:
        task = asyncio.create_task(_request_uncached(url, query, ttl, key))
        _request_tasks[key] = task

        def discard_task(completed: asyncio.Task[dict[str, Any]]) -> None:
            if _request_tasks.get(key) is completed:
                _request_tasks.pop(key, None)

        task.add_done_callback(discard_task)
    return await asyncio.shield(task)


async def _request_uncached(url: str, query: dict[str, Any], ttl: int, key: str) -> dict[str, Any]:
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=12, transport=network.AsyncDNSFallbackTransport()) as client:
        for attempt in range(3):
            try:
                headers = None
                if url == REVERSE_GEOCODING_URL:
                    headers = {"User-Agent": "EcoPulse community reporting/0.2 (OpenStreetMap reverse geocoding)"}
                elif url == MET_FORECAST_URL:
                    headers = {"User-Agent": "EcoPulse-AI/1.0 (https://ecopulseaifnb.netlify.app)"}
                response = await client.get(url, params=query, headers=headers)
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    response.raise_for_status()
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Weather provider returned an invalid response")
                if ttl > 0:
                    _cache[key] = (time.monotonic(), payload)
                return payload
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    raise
                if 400 <= exc.response.status_code < 500 and exc.response.status_code != 429:
                    raise
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(max(1.0, 0.25 * (2 ** attempt)) if url == REVERSE_GEOCODING_URL else 0.25 * (2 ** attempt))
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(max(1.0, 0.25 * (2 ** attempt)) if url == REVERSE_GEOCODING_URL else 0.25 * (2 ** attempt))
    assert last_error is not None
    raise last_error


def _weather_description(code: int | None) -> str:
    return {0:"Clear",1:"Mainly clear",2:"Partly cloudy",3:"Overcast",45:"Fog",48:"Rime fog",51:"Light drizzle",53:"Drizzle",55:"Dense drizzle",61:"Light rain",63:"Rain",65:"Heavy rain",71:"Light snow",80:"Rain showers",81:"Rain showers",82:"Heavy rain showers",95:"Thunderstorm",96:"Thunderstorm with hail",99:"Thunderstorm with hail"}.get(code, "Weather conditions unavailable")


async def forecast(latitude: float, longitude: float, *, current_request: bool = False) -> dict[str, Any]:
    params = {
        "latitude": latitude, "longitude": longitude, "timezone": "Africa/Johannesburg",
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,wind_speed_10m,wind_direction_10m,wind_gusts_10m,cloud_cover,uv_index,weather_code",
        "hourly": "temperature_2m,apparent_temperature,precipitation_probability,precipitation,wind_speed_10m,wind_direction_10m,wind_gusts_10m,relative_humidity_2m,cloud_cover,uv_index,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,apparent_temperature_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max",
        "forecast_days": 7,
    }
    try:
        raw = await _request(FORECAST_URL, params, CURRENT_TTL if current_request else FORECAST_TTL)
    except httpx.HTTPError as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code != 429 and exc.response.status_code < 500:
            raise
        log.warning("Open-Meteo unavailable; using MET Norway forecast fallback")
        try:
            return await _met_forecast(latitude, longitude)
        except (httpx.HTTPError, ValueError, KeyError) as fallback_error:
            log.warning("MET Norway forecast fallback failed: %s", type(fallback_error).__name__)
            raise exc from fallback_error
    if "current" not in raw or "hourly" not in raw or "daily" not in raw:
        raise ValueError("Weather provider response is missing forecast fields")
    c, h, d = raw["current"], raw["hourly"], raw["daily"]
    current = {"temperature":c.get("temperature_2m"),"feels_like":c.get("apparent_temperature"),"humidity":c.get("relative_humidity_2m"),"precipitation":c.get("precipitation"),"wind_speed":c.get("wind_speed_10m"),"wind_direction":c.get("wind_direction_10m"),"wind_gusts":c.get("wind_gusts_10m"),"cloud_cover":c.get("cloud_cover"),"uv_index":c.get("uv_index"),"weather_code":c.get("weather_code"),"condition":_weather_description(c.get("weather_code"))}
    hourly_keys = {"time":"time","temperature":"temperature_2m","feels_like":"apparent_temperature","precipitation_probability":"precipitation_probability","precipitation":"precipitation","wind_speed":"wind_speed_10m","wind_direction":"wind_direction_10m","wind_gusts":"wind_gusts_10m","humidity":"relative_humidity_2m","cloud_cover":"cloud_cover","uv_index":"uv_index","weather_code":"weather_code"}
    hourly = [{out:h[src][i] for out,src in hourly_keys.items() if src in h and i < len(h[src])} for i in range(len(h.get("time", [])))]
    daily_keys = {"date":"time","weather_code":"weather_code","high":"temperature_2m_max","low":"temperature_2m_min","feels_like_high":"apparent_temperature_max","feels_like_low":"apparent_temperature_min","precipitation_sum":"precipitation_sum","precipitation_probability_max":"precipitation_probability_max","wind_speed_max":"wind_speed_10m_max","wind_gusts_max":"wind_gusts_10m_max"}
    daily = [{out:d[src][i] for out,src in daily_keys.items() if src in d and i < len(d[src])} for i in range(len(d.get("time", [])))]
    return {"location":{"latitude":raw.get("latitude",latitude),"longitude":raw.get("longitude",longitude),"timezone":raw.get("timezone","Africa/Johannesburg")},"current":current,"hourly":hourly,"daily":daily,"updated_at":c.get("time"),"source":"Open-Meteo Forecast API","data_kind":"modelled current conditions and forecast","units":{"temperature":"°C","wind_speed":"km/h","precipitation":"mm","humidity":"%","cloud_cover":"%","uv_index":"index"}}


def _met_weather_code(symbol: str | None) -> int | None:
    if not symbol:
        return None
    symbol = symbol.casefold()
    if "thunder" in symbol:
        return 95
    if "snow" in symbol or "sleet" in symbol:
        return 71
    if "heavyrain" in symbol:
        return 65
    if "rainshowers" in symbol:
        return 80
    if "rain" in symbol:
        return 61
    if "drizzle" in symbol:
        return 51
    if "fog" in symbol:
        return 45
    if "cloudy" in symbol:
        return 3 if "partly" not in symbol else 2
    if "fair" in symbol:
        return 1
    if "clearsky" in symbol:
        return 0
    return None


def _met_condition(symbol: str | None) -> str:
    descriptions = {
        0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
        45: "Fog", 51: "Drizzle", 61: "Rain", 65: "Heavy rain",
        71: "Snow", 80: "Rain showers", 95: "Thunderstorm",
    }
    return descriptions.get(_met_weather_code(symbol), "Weather conditions unavailable")


async def _met_forecast(latitude: float, longitude: float) -> dict[str, Any]:
    raw = await _request(MET_FORECAST_URL, {"lat": latitude, "lon": longitude}, FORECAST_TTL)
    timeseries = raw.get("properties", {}).get("timeseries")
    if not isinstance(timeseries, list) or not timeseries:
        raise ValueError("MET Norway response is missing forecast values")

    zone = ZoneInfo("Africa/Johannesburg")
    hourly = []
    days: dict[str, list[dict[str, Any]]] = {}
    for item in timeseries:
        data = item.get("data", {})
        instant = data.get("instant", {}).get("details", {})
        timestamp = datetime.fromisoformat(item["time"].replace("Z", "+00:00")).astimezone(zone)
        symbol = (
            data.get("next_1_hours", {}).get("summary", {}).get("symbol_code")
            or data.get("next_6_hours", {}).get("summary", {}).get("symbol_code")
            or data.get("next_12_hours", {}).get("summary", {}).get("symbol_code")
        )
        next_details = data.get("next_1_hours", {}).get("details", {})
        if not next_details:
            next_details = data.get("next_6_hours", {}).get("details", {})
        wind_speed = instant.get("wind_speed")
        wind_gusts = instant.get("wind_speed_of_gust")
        row = {
            "time": timestamp.isoformat(),
            "temperature": instant.get("air_temperature"),
            "feels_like": None,
            "precipitation_probability": None,
            "precipitation": next_details.get("precipitation_amount"),
            "wind_speed": round(wind_speed * 3.6, 1) if wind_speed is not None else None,
            "wind_direction": instant.get("wind_from_direction"),
            "wind_gusts": round(wind_gusts * 3.6, 1) if wind_gusts is not None else None,
            "humidity": instant.get("relative_humidity"),
            "cloud_cover": instant.get("cloud_area_fraction"),
            "uv_index": None,
            "weather_code": _met_weather_code(symbol),
        }
        hourly.append(row)
        days.setdefault(timestamp.date().isoformat(), []).append(row)

    first_data = timeseries[0].get("data", {})
    current_details = first_data.get("instant", {}).get("details", {})
    current_symbol = (
        first_data.get("next_1_hours", {}).get("summary", {}).get("symbol_code")
        or first_data.get("next_6_hours", {}).get("summary", {}).get("symbol_code")
        or first_data.get("next_12_hours", {}).get("summary", {}).get("symbol_code")
    )
    speed = current_details.get("wind_speed")
    gusts = current_details.get("wind_speed_of_gust")
    daily = []
    for day, rows in list(days.items())[:7]:
        temperatures = [row["temperature"] for row in rows if row["temperature"] is not None]
        winds = [row["wind_speed"] for row in rows if row["wind_speed"] is not None]
        gust_values = [row["wind_gusts"] for row in rows if row["wind_gusts"] is not None]
        rainfall = [row["precipitation"] for row in rows if row["precipitation"] is not None]
        daily.append({
            "date": day,
            "weather_code": next((row["weather_code"] for row in rows if row["weather_code"] is not None), None),
            "high": max(temperatures) if temperatures else None,
            "low": min(temperatures) if temperatures else None,
            "feels_like_high": None,
            "feels_like_low": None,
            "precipitation_sum": sum(rainfall) if rainfall else None,
            "precipitation_probability_max": None,
            "wind_speed_max": max(winds) if winds else None,
            "wind_gusts_max": max(gust_values) if gust_values else None,
        })

    current = {
        "temperature": current_details.get("air_temperature"),
        "feels_like": None,
        "humidity": current_details.get("relative_humidity"),
        "precipitation": first_data.get("next_1_hours", {}).get("details", {}).get("precipitation_amount"),
        "wind_speed": round(speed * 3.6, 1) if speed is not None else None,
        "wind_direction": current_details.get("wind_from_direction"),
        "wind_gusts": round(gusts * 3.6, 1) if gusts is not None else None,
        "cloud_cover": current_details.get("cloud_area_fraction"),
        "uv_index": None,
        "weather_code": _met_weather_code(current_symbol),
        "condition": _met_condition(current_symbol),
    }
    updated_at = datetime.fromisoformat(timeseries[0]["time"].replace("Z", "+00:00")).astimezone(zone).isoformat()
    return {
        "location": {"latitude": latitude, "longitude": longitude, "timezone": "Africa/Johannesburg"},
        "current": current,
        "hourly": hourly,
        "daily": daily,
        "updated_at": updated_at,
        "source": "MET Norway Locationforecast API (fallback)",
        "data_kind": "modelled current conditions and forecast",
        "units": {"temperature": "°C", "wind_speed": "km/h", "precipitation": "mm", "humidity": "%", "cloud_cover": "%", "uv_index": "index"},
    }


async def history(latitude: float, longitude: float, start_date: date, end_date: date) -> dict[str, Any]:
    raw = await _request(ARCHIVE_URL, {"latitude":latitude,"longitude":longitude,"start_date":start_date.isoformat(),"end_date":end_date.isoformat(),"timezone":"Africa/Johannesburg","daily":"temperature_2m_mean,temperature_2m_max,temperature_2m_min,precipitation_sum"}, HISTORY_TTL)
    if "daily" not in raw:
        raise ValueError("Historical weather response is missing daily values")
    daily=raw["daily"]
    rows=[]
    for i,day in enumerate(daily.get("time", [])):
        rows.append({"date":day,"temperature_mean":_at(daily,"temperature_2m_mean",i),"temperature_max":_at(daily,"temperature_2m_max",i),"temperature_min":_at(daily,"temperature_2m_min",i),"precipitation_sum":_at(daily,"precipitation_sum",i)})
    return {"location":{"latitude":raw.get("latitude",latitude),"longitude":raw.get("longitude",longitude)},"daily":rows,"source":"Open-Meteo Historical Weather API (ERA5 reanalysis)","data_kind":"reanalysis, not a local station observation","updated_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}


def _at(data: dict[str, Any], key: str, index: int):
    values=data.get(key, [])
    return values[index] if index < len(values) else None


async def search_locations(name: str) -> list[dict[str, Any]]:
    raw=await _request(GEOCODING_URL,{"name":name,"count":10,"language":"en","format":"json","countryCode":"ZA"},86400)
    return [{"name":x.get("name"),"admin1":x.get("admin1"),"admin2":x.get("admin2"),"admin3":x.get("admin3"),"country":x.get("country"),"latitude":x.get("latitude"),"longitude":x.get("longitude"),"timezone":x.get("timezone")} for x in raw.get("results",[]) if x.get("latitude") is not None and x.get("longitude") is not None]


def _municipality_match_key(value: str) -> str:
    normalized = value.casefold().strip()
    normalized = re.sub(r"^(city|municipality) of\s+", "", normalized)
    normalized = re.sub(r"\s+(metropolitan|district|local)?\s*municipality$", "", normalized)
    normalized = re.sub(r"\s+metro$", "", normalized)
    return re.sub(r"[^a-z0-9]", "", normalized)


async def municipality_coordinates(name: str, province: str) -> dict[str, float] | None:
    """Find an approximate populated-place coordinate within the selected municipality."""
    search_terms = [name]
    simplified = re.sub(r"^(city|municipality) of\s+", "", name, flags=re.IGNORECASE).strip()
    simplified = re.sub(r"\s+(metropolitan|district|local)?\s*municipality$", "", simplified, flags=re.IGNORECASE).strip()
    if simplified and simplified.casefold() != name.casefold():
        search_terms.append(simplified)

    target = _municipality_match_key(name)
    for term in search_terms:
        results = await search_locations(term)
        matches = [
            result for result in results
            if result.get("admin1", "").casefold() == province.casefold()
            and target
            and any(
                target == _municipality_match_key(str(result.get(field) or ""))
                for field in ("admin2", "admin3")
            )
        ]
        if matches:
            selected = matches[0]
            return {"latitude": float(selected["latitude"]), "longitude": float(selected["longitude"])}
    return None


async def reverse_location(latitude: float, longitude: float) -> dict[str, Any]:
    """Return a privacy-conscious nearby label and address fields for a map point."""
    # Four decimals is roughly an 11 m grid: close enough for a nearby street
    # label, while avoiding a cache of exact GPS coordinates.
    lat, lon = round(latitude, 4), round(longitude, 4)
    key = f"{lat:.4f},{lon:.4f}"
    cached = _reverse_cache.get(key)
    if cached and time.monotonic() - cached[0] < 86400:
        return dict(cached[1])
    task = _reverse_request_tasks.get(key)
    if task is None:
        # A caller can stop waiting while the upstream request is still being
        # retried. Keep the pacing lock in this shared task until it finishes.
        task = asyncio.create_task(_reverse_location_uncached(lat, lon, key))
        _reverse_request_tasks[key] = task

        def discard_task(completed: asyncio.Task[dict[str, Any]]) -> None:
            if _reverse_request_tasks.get(key) is completed:
                _reverse_request_tasks.pop(key, None)
            # Retrieve failures even when every waiting caller timed out.
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(discard_task)
    return dict(await asyncio.shield(task))


async def _reverse_location_uncached(lat: float, lon: float, key: str) -> dict[str, Any]:
    global _last_reverse_request
    async with _reverse_request_lock:
        cached = _reverse_cache.get(key)
        if cached and time.monotonic() - cached[0] < 86400:
            return cached[1]
        wait = 1.0 - (time.monotonic() - _last_reverse_request)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_reverse_request = time.monotonic()
        params = {"lat": lat, "lon": lon, "format": "jsonv2", "addressdetails": 1, "zoom": 18}
        raw = await _request(REVERSE_GEOCODING_URL, params, 0)
    address = raw.get("address") or {}
    locality = next((address.get(key) for key in (
        "suburb", "neighbourhood", "quarter", "village", "town", "city", "municipality", "hamlet"
    ) if address.get(key)), None)
    label_parts = []
    for value in (address.get("road") or address.get("pedestrian"), locality, address.get("state")):
        if value and value not in label_parts:
            label_parts.append(value)
    result = {
        "label": ", ".join(label_parts),
        "locality": locality,
        "province": address.get("state"),
        "municipality": address.get("municipality"),
        "municipality_candidates": list(dict.fromkeys(address[field] for field in
            ("municipality", "city", "city_district", "county", "state_district") if address.get(field))),
        "country_code": address.get("country_code"),
        "source": "OpenStreetMap Nominatim",
    }
    if len(_reverse_cache) >= 1000:
        _reverse_cache.pop(next(iter(_reverse_cache)))
    _reverse_cache[key] = (time.monotonic(), result)
    return result


async def clear_weather_cache() -> None:
    _cache.clear()
    _reverse_cache.clear()

"""Open-Meteo adapter with bounded response caching and consistent units."""
from __future__ import annotations

import asyncio
import os
import time
from datetime import date
from typing import Any

import httpx

FORECAST_TTL = int(os.getenv("WEATHER_FORECAST_CACHE_SECONDS", "1800"))
CURRENT_TTL = int(os.getenv("WEATHER_CURRENT_CACHE_SECONDS", "900"))
HISTORY_TTL = int(os.getenv("WEATHER_HISTORY_CACHE_SECONDS", "86400"))
FORECAST_URL = "https://customer-api.open-meteo.com/v1/forecast" if os.getenv("OPEN_METEO_API_KEY", "").strip() else "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://customer-api.open-meteo.com/v1/archive" if os.getenv("OPEN_METEO_API_KEY", "").strip() else "https://archive-api.open-meteo.com/v1/archive"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
API_KEY = os.getenv("OPEN_METEO_API_KEY", "").strip()
_cache: dict[str, tuple[float, Any]] = {}


async def _request(url: str, params: dict[str, Any], ttl: int) -> dict[str, Any]:
    query = dict(params)
    if API_KEY and "customer-api.open-meteo.com" in url:
        query["apikey"] = API_KEY
    key = url + "?" + "&".join(f"{k}={query[k]}" for k in sorted(query))
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < ttl:
        return cached[1]
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=12) as client:
        for attempt in range(3):
            try:
                response = await client.get(url, params=query)
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    response.raise_for_status()
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Weather provider returned an invalid response")
                _cache[key] = (time.monotonic(), payload)
                return payload
            except httpx.HTTPStatusError as exc:
                if 400 <= exc.response.status_code < 500 and exc.response.status_code != 429:
                    raise
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.25 * (2 ** attempt))
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.25 * (2 ** attempt))
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
    raw = await _request(FORECAST_URL, params, CURRENT_TTL if current_request else FORECAST_TTL)
    if "current" not in raw or "hourly" not in raw or "daily" not in raw:
        raise ValueError("Weather provider response is missing forecast fields")
    c, h, d = raw["current"], raw["hourly"], raw["daily"]
    current = {"temperature":c.get("temperature_2m"),"feels_like":c.get("apparent_temperature"),"humidity":c.get("relative_humidity_2m"),"precipitation":c.get("precipitation"),"wind_speed":c.get("wind_speed_10m"),"wind_direction":c.get("wind_direction_10m"),"wind_gusts":c.get("wind_gusts_10m"),"cloud_cover":c.get("cloud_cover"),"uv_index":c.get("uv_index"),"weather_code":c.get("weather_code"),"condition":_weather_description(c.get("weather_code"))}
    hourly_keys = {"time":"time","temperature":"temperature_2m","feels_like":"apparent_temperature","precipitation_probability":"precipitation_probability","precipitation":"precipitation","wind_speed":"wind_speed_10m","wind_direction":"wind_direction_10m","wind_gusts":"wind_gusts_10m","humidity":"relative_humidity_2m","cloud_cover":"cloud_cover","uv_index":"uv_index","weather_code":"weather_code"}
    hourly = [{out:h[src][i] for out,src in hourly_keys.items() if src in h and i < len(h[src])} for i in range(len(h.get("time", [])))]
    daily_keys = {"date":"time","weather_code":"weather_code","high":"temperature_2m_max","low":"temperature_2m_min","feels_like_high":"apparent_temperature_max","feels_like_low":"apparent_temperature_min","precipitation_sum":"precipitation_sum","precipitation_probability_max":"precipitation_probability_max","wind_speed_max":"wind_speed_10m_max","wind_gusts_max":"wind_gusts_10m_max"}
    daily = [{out:d[src][i] for out,src in daily_keys.items() if src in d and i < len(d[src])} for i in range(len(d.get("time", [])))]
    return {"location":{"latitude":raw.get("latitude",latitude),"longitude":raw.get("longitude",longitude),"timezone":raw.get("timezone","Africa/Johannesburg")},"current":current,"hourly":hourly,"daily":daily,"updated_at":c.get("time"),"source":"Open-Meteo Forecast API","data_kind":"modelled current conditions and forecast","units":{"temperature":"°C","wind_speed":"km/h","precipitation":"mm","humidity":"%","cloud_cover":"%","uv_index":"index"}}


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
    return [{"name":x.get("name"),"admin1":x.get("admin1"),"admin2":x.get("admin2"),"country":x.get("country"),"latitude":x.get("latitude"),"longitude":x.get("longitude"),"timezone":x.get("timezone")} for x in raw.get("results",[]) if x.get("latitude") is not None and x.get("longitude") is not None]


async def clear_weather_cache() -> None:
    _cache.clear()

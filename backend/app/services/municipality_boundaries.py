"""South African municipal boundary lookup from the Municipal Demarcation Board.

The service returns the local/metro municipality containing a rounded point
from the MDB reference layer used by EcoPulse's municipality directory.
Positive results are cached in process for a day; coordinates are never
written to a profile.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from . import network

MDB_QUERY_URL = (
    "https://services7.arcgis.com/oeoyTUJC8HEeYsRB/arcgis/rest/services/"
    "MDB_L_ocal_Municipalities_2026/FeatureServer/0/query"
)
CACHE_TTL_SECONDS = 86_400
_cache: dict[str, tuple[float, dict[str, str]]] = {}
_lookups: dict[str, asyncio.Task[dict[str, str] | None]] = {}


async def municipality_at_point(latitude: float, longitude: float) -> dict[str, str] | None:
    """Return a unique official municipality/province match, if available."""
    # Match the location granularity already used by reverse geocoding and
    # avoid retaining a more precise device location in this service's cache.
    lat, lon = round(latitude, 4), round(longitude, 4)
    key = f"{lat:.4f},{lon:.4f}"
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
        return dict(cached[1])

    task = _lookups.get(key)
    if task is None:
        task = asyncio.create_task(_query_boundary(lat, lon, key))
        _lookups[key] = task

        def discard(completed: asyncio.Task[dict[str, str] | None]) -> None:
            if _lookups.get(key) is completed:
                _lookups.pop(key, None)
            # A disconnected caller may leave this shielded lookup running.
            # Retrieve errors even when no waiter remains to observe them.
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(discard)
    result = await asyncio.shield(task)
    return dict(result) if result else None


async def _query_boundary(latitude: float, longitude: float, key: str) -> dict[str, str] | None:
    params = {
        "geometry": f"{longitude:.4f},{latitude:.4f}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "MUNICNAME,PROVINCE,CATEGORY,NAMECODE",
        "returnGeometry": "false",
        "f": "json",
    }
    async with httpx.AsyncClient(
        timeout=10,
        transport=network.AsyncDNSFallbackTransport(),
    ) as client:
        response = await client.get(
            MDB_QUERY_URL,
            params=params,
            headers={"User-Agent": "EcoPulse South African community location lookup/1.0"},
        )
        response.raise_for_status()
        payload: Any = response.json()

    if not isinstance(payload, dict):
        raise ValueError("Municipal boundary provider returned an invalid response")
    if payload.get("error"):
        raise ValueError("Municipal boundary provider returned an error")
    features = payload.get("features")
    if not isinstance(features, list):
        raise ValueError("Municipal boundary provider returned invalid features")

    matches: dict[tuple[str, str], dict[str, str]] = {}
    for feature in features:
        if not isinstance(feature, dict) or not isinstance(feature.get("attributes"), dict):
            raise ValueError("Municipal boundary provider returned invalid attributes")
        attributes = feature["attributes"]
        name = str(attributes.get("MUNICNAME") or "").strip()
        province = str(attributes.get("PROVINCE") or "").strip()
        category = str(attributes.get("CATEGORY") or "").strip().upper()
        if name and province and category in {"A", "B"}:
            matches[(name.casefold(), province.casefold())] = {
                "name": name,
                "province": province,
                "source": "Municipal Demarcation Board 2026",
            }

    # Boundary-edge points can intersect two features. Never guess in that
    # case; let the resident choose their municipality from the verified list.
    if len(matches) != 1:
        return None
    result = next(iter(matches.values()))
    if len(_cache) >= 2000:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.monotonic(), result)
    return result

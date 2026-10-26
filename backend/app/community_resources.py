"""Resident resource map: verified public places and consented, coarse issue pins."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .security import postgres_connection, require_roles
from .services import network, weather_service

router = APIRouter(prefix="/api/v1", tags=["resident community resources"])
log = logging.getLogger(__name__)

RESOURCE_RADIUS_METRES = 20_000
RESOURCE_CACHE_SECONDS = 24 * 60 * 60
PARTIAL_CACHE_SECONDS = 6 * 60 * 60
FAILURE_CACHE_SECONDS = 15 * 60
MAX_EXTERNAL_RESOURCES = 1_000
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_BACKUP_URL = "https://overpass.private.coffee/api/interpreter"
HEALTH_FACILITIES_URL = (
    "https://hips-gis.health.gov.za/server/rest/services/"
    "Health_Facilities_Space_Location/FeatureServer/0/query"
)
_area_locks: dict[str, asyncio.Lock] = {}

OSM_CATEGORIES = {
    "police": ("police", "Police station"),
    "fire_station": ("emergency", "Fire station"),
    "ambulance_station": ("emergency", "Ambulance station"),
    "clinic": ("health", "Clinic"),
    "hospital": ("health", "Hospital"),
    "doctors": ("health", "Doctor or medical practice"),
    "pharmacy": ("pharmacy", "Pharmacy"),
    "school": ("education", "School"),
    "college": ("education", "College"),
    "university": ("education", "University"),
    "kindergarten": ("education", "Early learning centre"),
    "community_centre": ("community", "Community centre"),
    "library": ("community", "Library"),
    "post_office": ("community", "Post office"),
    "toilets": ("community", "Public toilets"),
    "drinking_water": ("community", "Drinking water"),
    "place_of_worship": ("community", "Place of worship"),
    "social_facility": ("community", "Social support service"),
    "recycling": ("community", "Recycling point"),
    "townhall": ("government", "Government office"),
    "courthouse": ("government", "Court"),
    "bus_station": ("transport", "Bus or taxi rank"),
    "bus_stop": ("transport", "Bus stop"),
    "taxi": ("transport", "Taxi rank"),
}
ACTIVE_REPORT_STATUSES = ("submitted", "acknowledged", "under_review", "verified", "in_progress")


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _load_area_data(user_id: str) -> dict[str, Any] | None:
    """Resolve map centre from the resident's selected community, never a URL-supplied location."""
    with postgres_connection() as connection:
        area = connection.execute(
            """select profile.municipality_id::text as municipality_id,
                      profile.community_id::text as community_id,
                      municipality.name as municipality_name, municipality.province,
                      coalesce(
                        case when community.centroid is not null
                          then extensions.st_y(community.centroid::extensions.geometry) end,
                        case when municipality.boundary is not null
                          then extensions.st_y(extensions.st_pointonsurface(municipality.boundary::extensions.geometry)) end
                      ) as latitude,
                      coalesce(
                        case when community.centroid is not null
                          then extensions.st_x(community.centroid::extensions.geometry) end,
                        case when municipality.boundary is not null
                          then extensions.st_x(extensions.st_pointonsurface(municipality.boundary::extensions.geometry)) end
                      ) as longitude
               from public.profiles profile
               join public.municipalities municipality on municipality.id=profile.municipality_id
               left join public.communities community
                 on community.id=profile.community_id and community.municipality_id=municipality.id
               where profile.id=%s and municipality.is_demonstration is false""",
            (user_id,),
        ).fetchone()
        if not area:
            return None

        area = dict(area)
        cache_key = f"{area['municipality_id']}:{area['community_id'] or 'municipality'}"
        cached = connection.execute(
            """select payload, fetched_at, expires_at, centre_latitude, centre_longitude
               from public.community_resource_cache where area_key=%s""",
            (cache_key,),
        ).fetchone()
        curated = connection.execute(
            """select id::text as id,name,category,description,
                      extensions.st_y(location::extensions.geometry) as latitude,
                      extensions.st_x(location::extensions.geometry) as longitude,
                      address,contact,source,verified,updated_at
               from public.community_resources
               where location is not null and extensions.st_dwithin(
                 location,
                 extensions.st_setsrid(extensions.st_makepoint(%s,%s),4326)::extensions.geography,
                 %s
               )
               order by verified desc,updated_at desc limit 250""",
            (area["longitude"], area["latitude"], RESOURCE_RADIUS_METRES),
        ).fetchall() if area["latitude"] is not None and area["longitude"] is not None else []

        issues = []
        if area["municipality_id"]:
            issues = connection.execute(
                """select category,status,
                          round(extensions.st_y(location::extensions.geometry)::numeric,3)::double precision as latitude,
                          round(extensions.st_x(location::extensions.geometry)::numeric,3)::double precision as longitude,
                          to_char(created_at at time zone 'Africa/Johannesburg','YYYY-MM-DD') as reported_on
                   from public.incidents
                   where municipality_id=%s and community_map_consent is true and location is not null
                     and status=any(%s::text[]) and created_at>=now()-interval '90 days'
                   order by created_at desc limit 500""",
                (area["municipality_id"], list(ACTIVE_REPORT_STATUSES)),
            ).fetchall()

    area["cache_key"] = cache_key
    area["cached"] = dict(cached) if cached else None
    area["curated"] = [dict(row) for row in curated]
    area["issues"] = [dict(row) for row in issues]
    return area


def _save_cache(area: dict[str, Any], payload: dict[str, Any], ttl: int) -> None:
    fetched_at = datetime.fromisoformat(payload["fetched_at"].replace("Z", "+00:00"))
    expires_at = datetime.now(timezone.utc).timestamp() + ttl
    with postgres_connection() as connection:
        connection.execute(
            """insert into public.community_resource_cache
                 (area_key,municipality_id,community_id,centre_latitude,centre_longitude,payload,fetched_at,expires_at)
               values(%s,%s,%s,%s,%s,%s::jsonb,%s,to_timestamp(%s))
               on conflict(area_key) do update set
                 municipality_id=excluded.municipality_id,community_id=excluded.community_id,
                 centre_latitude=excluded.centre_latitude,centre_longitude=excluded.centre_longitude,
                 payload=excluded.payload,fetched_at=excluded.fetched_at,expires_at=excluded.expires_at""",
            (area["cache_key"], area["municipality_id"], area["community_id"],
             area["latitude"], area["longitude"], json.dumps(payload, default=str),
             fetched_at, expires_at),
        )


def _osm_query(latitude: float, longitude: float) -> str:
    around = f"(around:{RESOURCE_RADIUS_METRES},{latitude:.6f},{longitude:.6f})"
    amenities = "police|fire_station|ambulance_station|community_centre|library|school|college|university|kindergarten|post_office|clinic|hospital|doctors|pharmacy|toilets|drinking_water|place_of_worship|social_facility|recycling|townhall|courthouse|bus_station|bus_stop|taxi"
    health = "clinic|hospital|doctor|pharmacy"
    return (
        "[out:json][timeout:15];("
        f'nwr[name][amenity~"^({amenities})$"]{around};'
        f'nwr[name][highway=bus_stop]{around};'
        f'nwr[name][shop=pharmacy]{around};'
        f'nwr[name][healthcare~"^({health})$"]{around};'
        f'nwr[name][emergency~"^(fire_service|ambulance_station)$"]{around};'
        ");out center tags 1000;"
    )


def _address(tags: dict[str, Any]) -> str | None:
    street = " ".join(part for part in (tags.get("addr:housenumber"), tags.get("addr:street")) if part)
    locality = tags.get("addr:suburb") or tags.get("addr:city") or tags.get("addr:town")
    return ", ".join(part for part in (street, locality) if part) or None


def _osm_resources(payload: dict[str, Any]) -> list[dict[str, Any]]:
    resources = []
    for element in payload.get("elements", []):
        tags = element.get("tags") or {}
        amenity = tags.get("amenity")
        healthcare = tags.get("healthcare")
        category_value = (
            "pharmacy" if tags.get("shop") == "pharmacy" else
            "bus_stop" if tags.get("highway") == "bus_stop" else
            amenity if amenity in OSM_CATEGORIES else
            "doctors" if healthcare == "doctor" else
            healthcare if healthcare in OSM_CATEGORIES else
            "emergency" if tags.get("emergency") in {"fire_service", "ambulance_station"} else None
        )
        if category_value == "emergency":
            label = "Ambulance station" if tags.get("emergency") == "ambulance_station" else "Fire station"
            category = "emergency"
        elif category_value:
            category, label = OSM_CATEGORIES[category_value]
        else:
            continue
        centre = element.get("center") or {}
        latitude = element.get("lat", centre.get("lat"))
        longitude = element.get("lon", centre.get("lon"))
        try:
            latitude, longitude = float(latitude), float(longitude)
        except (TypeError, ValueError):
            continue
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            continue
        element_type = element.get("type", "node")
        element_id = element.get("id")
        resources.append({
            "id": f"osm:{element_type}:{element_id}",
            "name": str(tags.get("name") or label)[:160],
            "category": category,
            "category_label": label,
            "facility_type": str(tags.get("healthcare") or amenity or tags.get("shop") or label).replace("_", " ")[:100],
            "latitude": latitude,
            "longitude": longitude,
            "address": _address(tags),
            "contact": None,
            "source": "OpenStreetMap contributors",
            "source_url": f"https://www.openstreetmap.org/{element_type}/{element_id}",
            "verified": False,
        })
    return resources


async def _fetch_osm(latitude: float, longitude: float) -> list[dict[str, Any]]:
    headers = {
        "User-Agent": "EcoPulse-AI/1.0 (+https://ecopulseaifnb.netlify.app)",
        "Accept": "application/json",
    }
    query = _osm_query(latitude, longitude)
    async with httpx.AsyncClient(timeout=22, transport=network.AsyncDNSFallbackTransport(), trust_env=False) as client:
        try:
            response = await client.post(OVERPASS_URL, data={"data": query}, headers=headers)
        except httpx.TransportError:
            # Try the independent endpoint after a network failure. A 429 response
            # is deliberately not retried elsewhere because that signals load.
            response = await client.post(OVERPASS_BACKUP_URL, data={"data": query}, headers=headers)
        if response.status_code >= 500:
            response = await client.post(OVERPASS_BACKUP_URL, data={"data": query}, headers=headers)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise ValueError("OpenStreetMap returned an invalid place list")
    return _osm_resources(payload)


async def _fetch_health_facilities(latitude: float, longitude: float) -> list[dict[str, Any]]:
    geometry = json.dumps({"x": longitude, "y": latitude, "spatialReference": {"wkid": 4326}})
    params = {
        "where": "1=1",
        "geometry": geometry,
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "distance": str(RESOURCE_RADIUS_METRES),
        "units": "esriSRUnit_Meter",
        "outFields": "id,facility_id,facility_name,facility_type,ownership_category,local_muncipality,province",
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": str(MAX_EXTERNAL_RESOURCES),
        "f": "json",
    }
    headers = {"User-Agent": "EcoPulse-AI/1.0 (South African community resource map)", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=18, transport=network.AsyncDNSFallbackTransport(), trust_env=False) as client:
        response = await client.get(HEALTH_FACILITIES_URL, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict) or payload.get("error"):
        raise ValueError("Department of Health service returned an invalid place list")
    resources = []
    for feature in payload.get("features", []):
        attributes = feature.get("attributes") or {}
        geometry = feature.get("geometry") or {}
        try:
            latitude_value = float(attributes.get("latitude") if attributes.get("latitude") is not None else geometry.get("y"))
            longitude_value = float(attributes.get("longitude") if attributes.get("longitude") is not None else geometry.get("x"))
        except (TypeError, ValueError):
            continue
        name = str(attributes.get("facility_name") or "Health facility").strip()[:160]
        facility_type = str(attributes.get("facility_type") or "Health facility").replace("_", " ")[:100]
        identifier = attributes.get("facility_id") or attributes.get("id") or name
        resources.append({
            "id": f"doh:{identifier}",
            "name": name,
            "category": "health",
            "category_label": facility_type,
            "facility_type": facility_type,
            "ownership": attributes.get("ownership_category"),
            "municipality": attributes.get("local_muncipality"),
            "province": attributes.get("province"),
            "latitude": latitude_value,
            "longitude": longitude_value,
            "address": None,
            "contact": None,
            "source": "South African Department of Health facility map",
            "source_url": "https://hips-gis.health.gov.za/server/rest/services/Health_Facilities_Space_Location/FeatureServer",
            "verified": True,
        })
    return resources


def _curated_resources(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    resources = []
    for row in rows:
        category_value = str(row["category"] or "community").lower().replace(" ", "_")
        category = (
            "health" if category_value in {"clinic", "hospital", "doctor", "doctors", "health_facility"} else
            "education" if category_value in {"school", "college", "university", "kindergarten"} else
            "transport" if category_value in {"taxi_rank", "bus_rank", "bus_station", "bus_stop"} else
            "government" if category_value in {"government", "government_office", "municipal_office", "court", "courthouse"} else
            "police" if category_value in {"police_station", "police"} else
            "emergency" if category_value in {"fire_station", "ambulance_station", "fire_service"} else
            category_value
        )
        resources.append({
            "id": f"ecopulse:{row['id']}",
            "name": str(row["name"])[:160],
            "category": category,
            "category_label": str(row["category"] or "Community resource").replace("_", " ").title(),
            "facility_type": str(row["category"] or "Community resource").replace("_", " ").title(),
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
            "address": row.get("address"),
            "contact": row.get("contact"),
            "description": row.get("description"),
            "source": str(row["source"] or "EcoPulse community directory"),
            "source_url": None,
            "verified": bool(row["verified"]),
            "updated_at": row["updated_at"].isoformat() if hasattr(row.get("updated_at"), "isoformat") else row.get("updated_at"),
        })
    return resources


def _merge_resources(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for resource in (item for group in groups for item in group):
        key = (str(resource.get("category", "")).casefold(), " ".join(str(resource.get("name", "")).casefold().split()))
        if key not in merged or (resource.get("verified") and not merged[key].get("verified")):
            merged[key] = resource
    return list(merged.values())


def _read_cache(area: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    cached = _json_value(area["cached"]["payload"]) if area.get("cached") else None
    if not cached:
        return None, False
    row = area["cached"]
    same_centre = (
        area["latitude"] is not None and area["longitude"] is not None
        and abs(float(row["centre_latitude"]) - float(area["latitude"])) < 0.0001
        and abs(float(row["centre_longitude"]) - float(area["longitude"])) < 0.0001
    )
    expires = row["expires_at"]
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return (cached, expires > datetime.now(timezone.utc)) if same_centre else (None, False)


async def _refresh_cache(area: dict[str, Any], stale: dict[str, Any] | None) -> dict[str, Any]:
    latitude, longitude = float(area["latitude"]), float(area["longitude"])
    results = await asyncio.gather(
        _fetch_health_facilities(latitude, longitude),
        _fetch_osm(latitude, longitude),
        return_exceptions=True,
    )
    health, osm = results
    for result in (health, osm):
        if isinstance(result, asyncio.CancelledError):
            raise result
    health_ok = isinstance(health, list)
    osm_ok = isinstance(osm, list)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    sources = [
        {"name": "South African Department of Health", "status": "available" if health_ok else "unavailable"},
        {"name": "OpenStreetMap contributors", "status": "available" if osm_ok else "unavailable"},
    ]
    if health_ok:
        places = health
    else:
        places = []
        log.warning("Department of Health map lookup failed (%s)", type(health).__name__)
    if osm_ok:
        places.extend(osm)
    else:
        log.warning("OpenStreetMap resource lookup failed (%s)", type(osm).__name__)

    if health_ok or osm_ok:
        payload = {
            "resources": _merge_resources(places),
            "sources": sources,
            "fetched_at": now,
            "stale": False,
        }
        ttl = RESOURCE_CACHE_SECONDS if health_ok and osm_ok else PARTIAL_CACHE_SECONDS
        _save_cache(area, payload, ttl)
        return payload

    if stale:
        payload = {
            **stale,
            "sources": sources,
            "checked_at": now,
            "stale": True,
        }
        # Keep old public place data visible and throttle repeated failures.
        _save_cache(area, payload, FAILURE_CACHE_SECONDS)
        return payload

    payload = {"resources": [], "sources": sources, "fetched_at": now, "stale": False}
    _save_cache(area, payload, FAILURE_CACHE_SECONDS)
    return payload


def _load_current_area(user_id: str) -> dict[str, Any] | None:
    return _load_area_data(user_id)


class CommunityShareRequest(BaseModel):
    share: bool


@router.get("/resident/community-resources")
async def resident_community_resources(user: dict[str, Any] = Depends(require_roles("resident"))):
    """Return nearby public facilities and only explicitly shared community issue pins."""
    area = await run_in_threadpool(_load_current_area, user["id"])
    if not area:
        raise HTTPException(422, "Choose your municipality in Profile & settings to see nearby community places.")
    if area["latitude"] is None or area["longitude"] is None:
        try:
            centre = await weather_service.municipality_coordinates(area["municipality_name"], area["province"])
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Community resource map could not locate the selected municipality (%s)", type(exc).__name__)
            centre = None
        if not centre:
            raise HTTPException(503, "Map coordinates are not available for your selected area yet.")
        area["latitude"], area["longitude"] = centre["latitude"], centre["longitude"]

    cached, fresh = _read_cache(area)
    if fresh:
        payload = cached
    else:
        lock = _area_locks.setdefault(area["cache_key"], asyncio.Lock())
        async with lock:
            area = await run_in_threadpool(_load_current_area, user["id"])
            if not area:
                raise HTTPException(422, "Choose your municipality in Profile & settings to see nearby community places.")
            # The map may rely on geocoded municipality coordinates when boundaries are absent.
            if area["latitude"] is None or area["longitude"] is None:
                area["latitude"], area["longitude"] = centre["latitude"], centre["longitude"]
            cached, fresh = _read_cache(area)
            payload = cached if fresh else await _refresh_cache(area, cached)

    return {
        "municipality": area["municipality_name"],
        "centre": {"latitude": float(area["latitude"]), "longitude": float(area["longitude"])},
        "radius_meters": RESOURCE_RADIUS_METRES,
        "resources": _merge_resources(_curated_resources(area["curated"]), payload.get("resources", [])),
        "issues": [dict(issue) for issue in area["issues"]],
        "sources": payload.get("sources", []),
        "fetched_at": payload.get("fetched_at"),
        "checked_at": payload.get("checked_at"),
        "stale": bool(payload.get("stale")),
    }


@router.put("/resident/community-reports/{incident_id}/sharing")
def update_community_report_sharing(
    incident_id: UUID,
    request: CommunityShareRequest,
    user: dict[str, Any] = Depends(require_roles("resident")),
):
    """Let a resident change sharing only for their own report."""
    with postgres_connection() as connection:
        report = connection.execute(
            "select location is not null as has_location from public.incidents where id=%s and reporter_id=%s",
            (str(incident_id), user["id"]),
        ).fetchone()
        if not report:
            raise HTTPException(404, "Report not found in your account.")
        if request.share and not report["has_location"]:
            raise HTTPException(422, "Add a map location before sharing this report.")
        saved = connection.execute(
            """update public.incidents set community_map_consent=%s
               where id=%s and reporter_id=%s returning community_map_consent""",
            (request.share, str(incident_id), user["id"]),
        ).fetchone()
    return {"shared": bool(saved["community_map_consent"])}

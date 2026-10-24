"""Authenticated, read-only EcoMove configuration and conservative journey matching."""
from __future__ import annotations

from datetime import datetime
import logging
from math import isfinite
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .security import current_user, postgres_connection
from .services.ecomove_matching import match_journeys


class EcoMoveAccessLogFilter(logging.Filter):
    """Keep request/status diagnostics without retaining residents' map coordinates."""

    _ecopulse_ecomove_redaction = True

    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn passes (client, method, full_path, http_version, status_code).
        # Leave unrelated/custom log formats and all other application paths alone.
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            path = record.args[2]
            if isinstance(path, str) and path.startswith("/api/v1/ecomove/"):
                record.args = (*record.args[:2], path.partition("?")[0], *record.args[3:])
        return True


def _install_access_log_filter() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(getattr(item, "_ecopulse_ecomove_redaction", False) for item in logger.filters):
        logger.addFilter(EcoMoveAccessLogFilter())


_install_access_log_filter()

router = APIRouter(prefix="/api/v1/ecomove", tags=["EcoMove"], dependencies=[Depends(current_user)])


class Coordinate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class TaxiStop(Coordinate):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)
    id: str
    name: str
    municipality_id: str
    stop_type: Literal["rank", "pickup", "dropoff", "interchange"]
    is_demo: bool
    is_interchange: bool


class RouteStop(TaxiStop):
    stop_sequence: int
    estimated_arrival_offset_minutes: float | None


class RouteGeometry(BaseModel):
    type: Literal["LineString"]
    coordinates: list[tuple[float, float]]


class TaxiRoute(BaseModel):
    id: str
    name: str
    origin: str
    destination: str
    municipality_id: str
    route_geometry: RouteGeometry
    direction: Literal["forward"]
    description: str
    color: str
    estimated_duration_minutes: float | None
    estimated_fare: float | None
    is_demo: bool
    data_source: str
    simulation_mode: Literal["once", "loop"]
    stops: list[RouteStop]


class NearbyRoute(TaxiRoute):
    nearest_pickup_stop: TaxiStop
    walking_distance_meters: float
    distance_meters: float
    simulated_vehicle_count: int


class NearbyStop(TaxiStop):
    distance_meters: float


class TaxiVehicle(BaseModel):
    id: str
    vehicle_code: str
    route_id: str
    simulation_speed: float
    initial_progress: float
    is_active: bool
    position_source: Literal["simulation"] = "simulation"


class JourneySearch(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    pickup: Coordinate
    destination: Coordinate
    radius_meters: float = Field(default=1000, ge=50, le=10000)
    municipality_id: UUID | None = None
    limit: int = Field(default=20, ge=1, le=50)


class JourneyLeg(BaseModel):
    route_id: str
    route_name: str
    pickup_stop: TaxiStop
    destination_stop: TaxiStop


class JourneyResult(BaseModel):
    route_id: str
    route_name: str
    transfers: Literal[0, 1]
    legs: list[JourneyLeg]
    pickup_stop: TaxiStop
    destination_stop: TaxiStop
    walking_distance_meters: float
    estimated_duration_minutes: float | None
    fare: float | None
    data_source: str
    is_demo: bool


class JourneyResponse(BaseModel):
    pickup: Coordinate
    destination: Coordinate
    routes: list[JourneyResult]


class TransportDisruption(BaseModel):
    id: str
    category: str
    title: str
    location_text: str
    latitude: float
    longitude: float
    created_at: datetime
    status: str
    route_ids: list[str]
    distance_meters: float


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


def _execute(sql: str, params: tuple[Any, ...] = (), *, fetch: str = "all"):
    """Run one bounded database statement and return its requested result shape."""
    try:
        with postgres_connection() as connection:
            connection.execute("set local statement_timeout = '8s'")
            cursor = connection.execute(sql, params)
            return cursor.fetchone() if fetch == "one" else cursor.fetchall()
    except HTTPException:
        raise
    except Exception as exc:
        # Never replace database failures with an empty/demo response or expose SQL/secrets.
        raise HTTPException(503, "EcoMove data is unavailable. Check the database connection and migration 010.") from exc


def _bbox_values(bbox: str | None) -> tuple[float, ...] | None:
    if bbox is None:
        return None
    try:
        values = tuple(float(value) for value in bbox.split(","))
        if len(values) != 4 or not all(isfinite(value) for value in values):
            raise ValueError
        west, south, east, north = values
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError
        return values
    except ValueError as exc:
        raise HTTPException(422, "bbox must contain west,south,east,north in valid coordinate order.") from exc


def _filters(alias: str, geography: str, municipality_id: UUID | None, bbox: str | None):
    # These SQL identifiers come from the fixed query definitions below; user values stay parameters.
    clauses, params = [], []
    if municipality_id is not None:
        clauses.append(f"{alias}.municipality_id=%s")
        params.append(municipality_id)
    bounds = _bbox_values(bbox)
    if bounds:
        clauses.append(f"extensions.st_intersects({alias}.{geography},extensions.st_makeenvelope(%s,%s,%s,%s,4326)::extensions.geography)")
        params.extend(bounds)
    return clauses, params


ROUTE_COLUMNS = """r.id::text,r.name,r.origin,r.destination,r.municipality_id::text,
 extensions.st_asgeojson(r.route_geometry::extensions.geometry)::json as route_geometry,
 r.direction,r.description,r.color,r.estimated_duration_minutes,r.estimated_fare,
 r.is_demo,r.data_source,r.simulation_mode"""
STOP_COLUMNS = """s.id::text,s.name,s.municipality_id::text,s.latitude,s.longitude,
 s.stop_type,s.is_demo,s.is_interchange"""


def _with_stops(rows: list[dict]) -> list[dict]:
    """Attach ordered stops to a page of routes with one shared lookup."""
    routes = [dict(row) for row in rows]
    if not routes:
        return routes
    stops = _execute(f"""select rs.route_id::text,{STOP_COLUMNS},rs.stop_sequence,
        rs.estimated_arrival_offset_minutes from public.taxi_route_stops rs
        join public.taxi_stops s on s.id=rs.stop_id
        where rs.route_id=any(%s::uuid[]) order by rs.route_id,rs.stop_sequence""",
        ([route["id"] for route in routes],))
    grouped: dict[str, list[dict]] = {}
    for stop in stops:
        stop = dict(stop)
        grouped.setdefault(stop.pop("route_id"), []).append(stop)
    for route in routes:
        route["stops"] = grouped.get(route["id"], [])
    return routes


def _page(items: list, total: int, limit: int, offset: int):
    """Keep collection endpoints on the same pagination contract."""
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/routes", response_model=Page[TaxiRoute])
def routes(
    municipality_id: UUID | None = None,
    bbox: str | None = Query(None, max_length=120),
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    clauses, params = _filters("r", "route_geometry", municipality_id, bbox)
    where = " and ".join(clauses) or "true"
    total = _execute(
        f"select count(*) as total from public.taxi_routes r where {where}",
        tuple(params),
        fetch="one",
    )["total"]
    rows = _execute(
        f"select {ROUTE_COLUMNS} from public.taxi_routes r where {where} "
        "order by r.name,r.id limit %s offset %s",
        (*params, limit, offset),
    )
    return _page(_with_stops(rows), total, limit, offset)


@router.get("/stops", response_model=Page[TaxiStop])
def stops(
    municipality_id: UUID | None = None,
    bbox: str | None = Query(None, max_length=120),
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    clauses, params = _filters("s", "location", municipality_id, bbox)
    where = " and ".join(clauses) or "true"
    total = _execute(
        f"select count(*) as total from public.taxi_stops s where {where}",
        tuple(params),
        fetch="one",
    )["total"]
    rows = _execute(
        f"select {STOP_COLUMNS} from public.taxi_stops s where {where} "
        "order by s.name,s.id limit %s offset %s",
        (*params, limit, offset),
    )
    return _page(rows, total, limit, offset)


@router.get("/stops/nearby", response_model=Page[NearbyStop])
def nearby_stops(
    latitude: float = Query(ge=-90, le=90, allow_inf_nan=False),
    longitude: float = Query(ge=-180, le=180, allow_inf_nan=False),
    radius_meters: float = Query(1000, ge=50, le=10000, allow_inf_nan=False),
    municipality_id: UUID | None = None,
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    clauses, params = _filters("s", "location", municipality_id, None)
    clauses.append("extensions.st_dwithin(s.location,p.location,%s)")
    where = " and ".join(clauses)
    # PostGIS points use longitude, latitude; remaining values match _filters order.
    parameters = (longitude, latitude, *params, radius_meters)
    prefix = "with p as (select extensions.st_setsrid(extensions.st_makepoint(%s,%s),4326)::extensions.geography as location)"
    total = _execute(f"{prefix} select count(*) as total from public.taxi_stops s cross join p where {where}", parameters, fetch="one")["total"]
    rows = _execute(f"""{prefix} select {STOP_COLUMNS},extensions.st_distance(s.location,p.location) as distance_meters
        from public.taxi_stops s cross join p where {where} order by distance_meters,s.id limit %s offset %s""",
        (*parameters, limit, offset))
    return _page(rows, total, limit, offset)


@router.get("/routes/nearby", response_model=Page[NearbyRoute])
def nearby_routes(
    latitude: float = Query(ge=-90, le=90, allow_inf_nan=False),
    longitude: float = Query(ge=-180, le=180, allow_inf_nan=False),
    radius_meters: float = Query(1000, ge=50, le=10000, allow_inf_nan=False),
    municipality_id: UUID | None = None,
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    clauses, params = _filters("r", "route_geometry", municipality_id, None)
    where = " and ".join(clauses) or "true"
    prefix = """with p as (select extensions.st_setsrid(extensions.st_makepoint(%s,%s),4326)::extensions.geography as location)"""
    nearest = f"""from public.taxi_routes r cross join p join lateral (
       select {STOP_COLUMNS},extensions.st_distance(s.location,p.location) as walking_distance_meters
       from public.taxi_route_stops rs join public.taxi_stops s on s.id=rs.stop_id
       where rs.route_id=r.id and s.stop_type in ('rank','pickup','interchange')
       and exists(select 1 from public.taxi_route_stops onward where onward.route_id=r.id and onward.stop_sequence>rs.stop_sequence)
       and extensions.st_dwithin(s.location,p.location,%s)
       order by walking_distance_meters,s.id limit 1
     ) pickup on true where {where}"""
    parameters = (longitude, latitude, radius_meters, *params)
    total = _execute(f"{prefix} select count(*) as total {nearest}", parameters, fetch="one")["total"]
    rows = _execute(f"""{prefix} select {ROUTE_COLUMNS},row_to_json(pickup) as nearest_pickup_stop,
       pickup.walking_distance_meters,extensions.st_distance(r.route_geometry,p.location) as distance_meters,
       (select count(*) from public.taxi_simulation_vehicles v where v.route_id=r.id and v.is_active) as simulated_vehicle_count
       {nearest} order by pickup.walking_distance_meters,r.name,r.id limit %s offset %s""", (*parameters, limit, offset))
    return _page(_with_stops(rows), total, limit, offset)


# Register the static /routes/nearby route before the UUID detail route.
@router.get("/routes/{route_id}", response_model=TaxiRoute)
def route_detail(route_id: UUID):
    rows = _execute(f"select {ROUTE_COLUMNS} from public.taxi_routes r where r.id=%s", (route_id,))
    if not rows:
        raise HTTPException(404, "This configured taxi route was not found.")
    return _with_stops(rows)[0]


@router.get("/simulation/vehicles", response_model=Page[TaxiVehicle])
def simulation_vehicles(
    route_id: UUID | None = None,
    municipality_id: UUID | None = None,
    bbox: str | None = Query(None, max_length=120),
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    clauses, params = _filters("r", "route_geometry", municipality_id, bbox)
    clauses.append("v.is_active")
    if route_id:
        clauses.append("v.route_id=%s")
        params.append(route_id)
    base = "from public.taxi_simulation_vehicles v join public.taxi_routes r on r.id=v.route_id where " + " and ".join(clauses)
    total = _execute(f"select count(*) as total {base}", tuple(params), fetch="one")["total"]
    rows = _execute(f"""select v.id::text,v.vehicle_code,v.route_id::text,v.simulation_speed,v.initial_progress,
        v.is_active,'simulation'::text as position_source {base} order by v.vehicle_code,v.id limit %s offset %s""",
        (*params, limit, offset))
    return _page(rows, total, limit, offset)


@router.post("/journey/search", response_model=JourneyResponse)
def search_journey(search: JourneySearch):
    # Both candidate legs must serve an endpoint. No arbitrary third-route expansion.
    # A hard cap avoids expensive or silently incomplete matching in dense searches.
    clauses, params = _filters("r", "route_geometry", search.municipality_id, None)
    clauses.append("""exists(select 1 from public.taxi_route_stops rs join public.taxi_stops s on s.id=rs.stop_id
        where rs.route_id=r.id and (
        extensions.st_dwithin(s.location,p.pickup,%s) or extensions.st_dwithin(s.location,p.destination,%s)))""")
    prefix = """with p as (select
        extensions.st_setsrid(extensions.st_makepoint(%s,%s),4326)::extensions.geography as pickup,
        extensions.st_setsrid(extensions.st_makepoint(%s,%s),4326)::extensions.geography as destination)"""
    # Cap candidates at 201 so a dense result is rejected explicitly instead of truncated.
    rows = _execute(f"""{prefix} select {ROUTE_COLUMNS} from public.taxi_routes r cross join p
        where {' and '.join(clauses)} order by r.id limit 201""",
        (search.pickup.longitude, search.pickup.latitude, search.destination.longitude, search.destination.latitude,
         *params, search.radius_meters, search.radius_meters))
    if len(rows) > 200:
        raise HTTPException(422, "Too many configured routes in this search. Choose a municipality or a smaller search radius.")
    pickup, destination = search.pickup.model_dump(), search.destination.model_dump()
    return {
        "pickup": pickup,
        "destination": destination,
        "routes": match_journeys(
            _with_stops(rows),
            pickup,
            destination,
            search.radius_meters,
            search.limit,
        ),
    }


@router.get("/disruptions", response_model=Page[TransportDisruption])
def disruptions(
    route_ids: str | None = Query(None, max_length=740),
    municipality_id: UUID | None = None,
    bbox: str | None = Query(None, max_length=120),
    radius_meters: float = Query(200, ge=25, le=1000, allow_inf_nan=False),
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    clauses, params = _filters("r", "route_geometry", municipality_id, bbox)
    if route_ids is not None:
        try:
            identifiers = [str(UUID(value.strip())) for value in route_ids.split(",")]
            if not 1 <= len(identifiers) <= 20:
                raise ValueError
        except ValueError as exc:
            raise HTTPException(422, "route_ids must contain between 1 and 20 comma-separated route UUIDs.") from exc
        clauses.append("r.id=any(%s::uuid[])")
        params.append(identifiers)
    route_where = " and ".join(clauses) or "true"
    # Use existing incident records and expose this allowlist only. No owner,
    # original descriptions, media paths, contact details or unconfirmed closures.
    prefix = f"""with selected_routes as (select r.id,r.municipality_id,r.route_geometry from public.taxi_routes r where {route_where}),
       nearby as (select i.id::text,i.category,i.title,i.location_text,
       extensions.st_y(i.location::extensions.geometry) as latitude,
       extensions.st_x(i.location::extensions.geometry) as longitude,i.created_at,i.status,
       array_agg(r.id::text order by r.id) as route_ids,
       min(extensions.st_distance(i.location,r.route_geometry)) as distance_meters
       from public.incidents i join selected_routes r on i.municipality_id=r.municipality_id
       and extensions.st_dwithin(i.location,r.route_geometry,%s)
       where i.location is not null and i.status not in ('resolved','closed','duplicate','rejected','unable_to_verify')
       and (i.category in ('roads','stormwater') or
            (i.category='environmental' and i.title ~* '(fallen|tree|obstruct|road)'))
       group by i.id)"""
    parameters = (*params, radius_meters)
    total = _execute(f"{prefix} select count(*) as total from nearby", parameters, fetch="one")["total"]
    rows = _execute(
        f"{prefix} select * from nearby "
        "order by distance_meters,created_at desc,id limit %s offset %s",
        (*parameters, limit, offset),
    )
    return _page(rows, total, limit, offset)

"""Match journeys using only configured stops, directions, and interchanges."""
from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from typing import Any

BOARDING_TYPES = {"rank", "pickup", "interchange"}
ALIGHTING_TYPES = {"rank", "dropoff", "interchange"}


def distance_meters(first: dict, second: dict) -> float:
    """Estimate straight-line distance between two geographic points."""
    lat1, lat2 = radians(first["latitude"]), radians(second["latitude"])
    delta_lat = lat2 - lat1
    delta_lon = radians(second["longitude"] - first["longitude"])
    value = (
        sin(delta_lat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    )
    # Floating-point rounding can put the haversine value just outside [0, 1].
    return 6_371_008.8 * 2 * asin(sqrt(min(1, max(0, value))))


def _duration(pickup: dict, destination: dict) -> float | None:
    start = pickup.get("estimated_arrival_offset_minutes")
    end = destination.get("estimated_arrival_offset_minutes")
    if start is None or end is None or end < start:
        return None
    return round(float(end) - float(start), 2)


def _leg(route: dict, pickup: dict, destination: dict) -> dict:
    return {
        "route_id": route["id"],
        "route_name": route["name"],
        "pickup_stop": pickup,
        "destination_stop": destination,
    }


def _result(legs: list[dict], routes: list[dict], walk: float) -> dict:
    durations = [_duration(leg["pickup_stop"], leg["destination_stop"]) for leg in legs]
    fares = [route.get("estimated_fare") for route in routes]
    return {
        "route_id": routes[0]["id"],
        "route_name": " → ".join(route["name"] for route in routes),
        "transfers": len(legs) - 1,
        "legs": legs,
        "pickup_stop": legs[0]["pickup_stop"],
        "destination_stop": legs[-1]["destination_stop"],
        "walking_distance_meters": round(walk, 1),
        "estimated_duration_minutes": (
            round(sum(durations), 2) if all(value is not None for value in durations) else None
        ),
        "fare": (
            round(sum(float(value) for value in fares), 2)
            if all(value is not None for value in fares)
            else None
        ),
        "data_source": "; ".join(dict.fromkeys(route["data_source"] for route in routes)),
        "is_demo": any(route["is_demo"] for route in routes),
    }


def _sort_key(result: dict) -> tuple:
    return (
        result["transfers"],
        result["walking_distance_meters"],
        result["estimated_duration_minutes"]
        if result["estimated_duration_minutes"] is not None
        else float("inf"),
        result["route_name"],
    )


def match_journeys(routes: list[dict[str, Any]], pickup: dict, destination: dict,
                   radius_meters: float, limit: int = 20) -> list[dict]:
    """Return best boarding pair per direct route, else best pair per transfer route pair.

    Each leg travels to a larger stop_sequence. Looped geometry never implies an
    unconfigured reverse journey. Walks are straight-line estimates, not footpaths.
    """
    candidates = []
    for route in routes:
        stops = sorted(route.get("stops", []), key=lambda stop: stop["stop_sequence"])
        board = [
            (stop, distance_meters(pickup, stop))
            for stop in stops
            if stop["stop_type"] in BOARDING_TYPES
        ]
        alight = [
            (stop, distance_meters(destination, stop))
            for stop in stops
            if stop["stop_type"] in ALIGHTING_TYPES
        ]
        board = [(stop, walk) for stop, walk in board if walk <= radius_meters]
        alight = [(stop, walk) for stop, walk in alight if walk <= radius_meters]
        candidates.append((route, stops, board, alight))

    # Prefer any valid direct route; transfers are considered only when none exist.
    direct = []
    for route, _, board, alight in candidates:
        options = [
            _result([_leg(route, start, end)], [route], start_walk + end_walk)
            for start, start_walk in board
            for end, end_walk in alight
            if start["stop_sequence"] < end["stop_sequence"]
        ]
        if options:
            direct.append(min(options, key=_sort_key))
    if direct:
        return sorted(direct, key=_sort_key)[:limit]

    transfers = []
    for first, first_stops, board, _ in candidates:
        if not board:
            continue
        interchanges = {
            stop["id"]: stop
            for stop in first_stops
            if stop.get("is_interchange") and stop["stop_type"] in ALIGHTING_TYPES
        }
        for second, second_stops, _, alight in candidates:
            if first["id"] == second["id"] or not alight:
                continue
            options = []
            for interchange in second_stops:
                if not interchange.get("is_interchange") or interchange["stop_type"] not in BOARDING_TYPES:
                    continue
                connection = interchanges.get(interchange["id"])
                if connection is None:
                    continue
                # Coordinates alone do not connect routes; the same configured stop ID must.
                for start, start_walk in board:
                    if start["stop_sequence"] >= connection["stop_sequence"]:
                        continue
                    for end, end_walk in alight:
                        if interchange["stop_sequence"] < end["stop_sequence"]:
                            options.append(
                                _result(
                                    [
                                        _leg(first, start, connection),
                                        _leg(second, interchange, end),
                                    ],
                                    [first, second],
                                    start_walk + end_walk,
                                )
                            )
            if options:
                transfers.append(min(options, key=_sort_key))
    return sorted(transfers, key=_sort_key)[:limit]

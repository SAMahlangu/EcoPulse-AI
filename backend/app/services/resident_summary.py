"""Resident overview projections from the canonical household consumption engines."""
from __future__ import annotations

from typing import Any


def consumption_summary(connection, user: dict[str, Any]) -> dict[str, Any]:
    # Import lazily: both routers depend on authentication, but neither depends
    # on this overview projection. Never read simulation sessions here.
    from .. import energy, water

    result = {}
    for kind, module, value_key, month_key, output_key, scale in (
        ("water", water, "litres", "month_kilolitres", "consumption_kilolitres", 1000),
        ("energy", energy, "kwh", "month_kwh", "consumption_kwh", 1),
    ):
        household = module._household(connection, user)
        snapshot = module._actual_dashboard(connection, household, days=30, persist=False)
        summary = snapshot["summary"]
        observed = summary["month_observed_days"]
        estimated = summary["month_estimated_days"]
        result[kind] = {
            "month": snapshot["as_of_date"][:7],
            output_key: summary[month_key],
            # Preserve the original field for clients; it now counts covered
            # consumption dates rather than overlapping meter intervals.
            "reading_count": observed + estimated,
            "observed_days": observed,
            "estimated_days": estimated,
            "estimated_cost": summary["estimated_cost_rand"],
            "coverage_note": summary["coverage_note"],
            "series": [
                {
                    "current_date": point["date"],
                    "consumption": round(point[value_key] / scale, 3) if point[value_key] is not None else None,
                    "is_estimated": point["is_estimated"],
                    "data_source": point["data_source"],
                }
                for point in snapshot["series"]
            ],
        }
    return result

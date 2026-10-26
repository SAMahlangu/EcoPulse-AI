"""Deterministic household water accounting; no model performs arithmetic here."""
from __future__ import annotations

import math
import random
import statistics
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo


SCENARIOS = {"normal", "excessive", "possible_leak", "weekend", "gradual_increase", "saving"}


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _number(value: Any, default: float = 0) -> float:
    number = float(value) if value is not None else default
    if not math.isfinite(number) or number < 0:
        raise ValueError("Water values must be finite and non-negative.")
    return number


def _timestamp(value: Any, zone: ZoneInfo) -> datetime:
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Meter timestamps must include a timezone.")
    return stamp.astimezone(zone)


def _days(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _round(value: float | None, digits: int = 2):
    return round(value, digits) if value is not None else None


def _average(points: list[dict]) -> float | None:
    values = [point["litres"] for point in points if point.get("litres") is not None]
    return statistics.mean(values) if values else None


def _allocate_interval(start: datetime, end: datetime, litres: float, lower: date, upper: date,
                       zone: ZoneInfo) -> dict[date, dict]:
    """Allocate only recorded volume. Multi-day allocations are always estimates."""
    seconds = end.timestamp() - start.timestamp()
    if seconds <= 0:
        return {}
    last_day = (end - timedelta(microseconds=1)).date()
    spans_days = start.date() != last_day
    result = {}
    for day in _days(max(start.date(), lower), min(last_day, upper)):
        midnight = datetime.combine(day, time.min, zone)
        following = datetime.combine(day + timedelta(days=1), time.min, zone)
        overlap = max(0, min(end.timestamp(), following.timestamp()) - max(start.timestamp(), midnight.timestamp()))
        if overlap:
            result[day] = {"litres": litres * overlap / seconds, "seconds": overlap,
                           "split_estimate": spans_days,
                           "day_seconds": following.timestamp() - midnight.timestamp()}
    return result


def _meter_days(rows: list[dict], lower: date, upper: date, zone: ZoneInfo) -> dict[date, dict]:
    result: dict[date, dict] = {}
    ordered = sorted(rows, key=lambda row: _timestamp(row["reading_at"], zone))
    for previous, current in zip(ordered, ordered[1:]):
        if current.get("is_reset"):
            # A new meter/rollover is a baseline, never an assumed zero reading.
            continue
        delta = _number(current["cumulative_kl"]) - _number(previous["cumulative_kl"])
        if delta < -0.0000001:
            raise ValueError("A decreasing cumulative reading requires an explicit meter reset.")
        allocated = _allocate_interval(_timestamp(previous["reading_at"], zone),
                                       _timestamp(current["reading_at"], zone), max(0, delta) * 1000,
                                       lower, upper, zone)
        for day, part in allocated.items():
            point = result.setdefault(day, {"litres": 0, "seconds": 0, "split_estimate": False,
                                            "day_seconds": part["day_seconds"]})
            point["litres"] += part["litres"]
            point["seconds"] += part["seconds"]
            point["split_estimate"] |= part["split_estimate"]
    return result


def _legacy_days(rows: list[dict], lower: date, upper: date, zone: ZoneInfo) -> dict[date, dict]:
    result = {}
    # Overlapping historical periods are alternative observations, never additive.
    for row in sorted(rows, key=lambda row: str(row.get("created_at", ""))):
        start = datetime.combine(_date(row["previous_date"]), time.min, zone)
        end = datetime.combine(_date(row.get("reading_date", row.get("current_date"))), time.min, zone)
        previous = _number(row.get("previous_value", row.get("previous")))
        current = _number(row.get("current_value", row.get("current")))
        if current < previous:
            raise ValueError("Historical water readings contain an invalid decreasing meter.")
        for day, part in _allocate_interval(start, end, (current - previous) * 1000, lower, upper, zone).items():
            result[day] = part
    return result


def build_dashboard(household: dict, daily_records: list[dict], meter_readings: list[dict],
                    legacy_readings: list[dict], *, start_date: date, end_date: date,
                    as_of_date: date | None = None, is_simulation: bool = False) -> dict:
    zone = ZoneInfo(household.get("timezone") or "Africa/Johannesburg")
    today = as_of_date or datetime.now(zone).date()
    start_date, end_date = _date(start_date), _date(end_date)
    if end_date < start_date or (end_date - start_date).days > 89:
        raise ValueError("Select an ordered date range of at most 90 days.")
    lower = min(start_date - timedelta(days=30), today - timedelta(days=30), today.replace(day=1))
    upper = max(today, end_date)
    target = _number(household.get("daily_target_litres"), 600)
    if target <= 0:
        raise ValueError("Daily target must be greater than zero.")
    tariff = _number(household.get("tariff_per_kl"))
    by_day: dict[date, dict] = {}
    for source, values in (("legacy_meter", _legacy_days(legacy_readings, lower, upper, zone)),
                           ("meter", _meter_days(meter_readings, lower, upper, zone))):
        for day, part in values.items():
            estimated = part["split_estimate"] or part["seconds"] < part["day_seconds"] - 0.01
            by_day[day] = {"id": None, "date": day.isoformat(), "litres": part["litres"],
                           "target_litres": target, "data_source": "estimated" if estimated else "meter",
                           "is_estimated": estimated, "origin": source,
                           "coverage_fraction": min(1, part["seconds"] / part["day_seconds"])}
    for record in daily_records:
        day = _date(record.get("date", record.get("consumption_date")))
        if not lower <= day <= upper:
            continue
        by_day[day] = {"id": str(record["id"]) if record.get("id") else None,
                       "date": day.isoformat(), "litres": _number(record.get("litres", record.get("consumption_litres"))),
                       "target_litres": _number(record.get("target_litres"), target),
                       "data_source": "simulation" if is_simulation else "manual",
                       "is_estimated": False, "coverage_fraction": 1}
    all_points: dict[date, dict] = {}
    alerts, reports = [], []
    for day in _days(lower, upper):
        point = by_day.get(day, {"id": None, "date": day.isoformat(), "litres": None,
                                 "target_litres": target, "data_source": "missing", "is_estimated": False,
                                 "coverage_fraction": 0})
        amount = point["litres"]
        goal = point["target_litres"] or target
        point.update(is_simulation=is_simulation, anomalous=False, flags=[], status="No data",
                     difference_litres=None, percentage_vs_target=None)
        previous_week = [all_points[d] for d in _days(day - timedelta(days=7), day - timedelta(days=1)) if d in all_points]
        prior_values = [p["litres"] for p in previous_week if p["litres"] is not None and not p["is_estimated"]]
        baseline = statistics.mean(prior_values) if prior_values else None
        recent = [all_points[d] for d in _days(day - timedelta(days=6), day - timedelta(days=1)) if d in all_points] + [point]
        month_context = [p for d, p in all_points.items() if day - timedelta(days=29) <= d < day] + [point]
        previous_day = all_points.get(day - timedelta(days=1), {})
        point.update(moving_average_litres=_round(_average(recent)),
                     previous_day_litres=_round(previous_day.get("litres")),
                     prior_seven_day_average_litres=_round(baseline),
                     thirty_day_average_litres=_round(_average(month_context)),
                     thirty_day_known_days=sum(p.get("litres") is not None for p in month_context))
        if amount is not None:
            diff = amount - goal
            point.update(difference_litres=_round(diff), percentage_vs_target=_round(diff / goal * 100),
                         status="Above Target" if amount > goal else "Approaching Target" if amount >= goal * 0.9 else "Normal")
            if diff > 0:
                point["flags"].append("above_target")
            unusual = (not point["is_estimated"] and len(prior_values) >= 3 and baseline is not None
                       and amount > baseline * 1.5 and amount - baseline >= max(100, goal * 0.2))
            repeat = all(all_points.get(day - timedelta(days=offset), {}).get("litres") is not None
                         and not all_points[day - timedelta(days=offset)]["is_estimated"]
                         and all_points[day - timedelta(days=offset)]["litres"] > all_points[day - timedelta(days=offset)]["target_litres"]
                         for offset in (1, 2)) and diff > 0 and not point["is_estimated"]
            possible_leak = (unusual and amount >= goal * 2) or (
                repeat and amount >= goal * 1.5 and all(
                    all_points[day - timedelta(days=offset)]["litres"] >= all_points[day - timedelta(days=offset)]["target_litres"] * 1.5
                    for offset in (1, 2)))
            if unusual:
                point["flags"].append("unusual_increase")
                point["status"] = "Unusual Consumption"
            if repeat:
                point["flags"].append("repeated_excess")
            if possible_leak:
                point["flags"].append("possible_leak")
                point["status"] = "Possible Leak"
            if not point["is_estimated"] and len(prior_values) >= 3 and baseline and amount < baseline * 0.5 and baseline - amount >= max(100, goal * 0.2):
                point["flags"].append("significant_decrease")
            point["anomalous"] = unusual or possible_leak
            prefix = "Simulated consumption" if is_simulation else "Estimated allocated consumption" if point["is_estimated"] else "Recorded consumption"
            messages = {
                "above_target": f"{prefix} was {amount:,.1f} L on {day.isoformat()}, {diff:,.1f} L ({diff / goal * 100:.1f}%) above the {goal:,.1f} L target.",
                "unusual_increase": "Consumption is over one and a half times the recent recorded average, with a substantial absolute increase. Review unusual activities.",
                "repeated_excess": "Recorded consumption exceeded its daily target on three consecutive calendar days.",
                "possible_leak": "A sharp or sustained consumption increase warrants investigation. Household activities or a plumbing leak may contribute; consumption alone does not confirm a leak.",
                "significant_decrease": "Recorded consumption fell to less than half its recent average. Check the reading and consider whether household activities changed.",
            }
            for flag in point["flags"]:
                alerts.append({"consumption_date": day.isoformat(), "alert_type": flag,
                               "severity": "high" if flag == "possible_leak" else "warning" if flag != "significant_decrease" else "info",
                               "message": messages[flag], "is_simulation": is_simulation})
            reports.append({"date": day.isoformat(), "litres": _round(amount), "target_litres": goal,
                            "difference_litres": _round(diff), "status": point["status"],
                            "is_estimated": point["is_estimated"], "is_simulation": is_simulation})
        point["litres"] = _round(amount)
        all_points[day] = point
    current = all_points.get(today, {})
    yesterday = all_points.get(today - timedelta(days=1), {}).get("litres")
    amount = current.get("litres")
    week = [p for d, p in all_points.items() if today - timedelta(days=6) <= d <= today and p["litres"] is not None]
    month = [p for d, p in all_points.items() if today.replace(day=1) <= d <= today and p["litres"] is not None]
    month_total = sum(p["litres"] for p in month) if month else None
    measured_week = [p for p in week if not p["is_estimated"]]
    score = (round(statistics.mean(100 if p["litres"] == 0 else min(100, 100 * p["target_litres"] / p["litres"]) for p in measured_week))
             if measured_week else None)
    diff_yesterday = amount - yesterday if amount is not None and yesterday is not None else None
    summary = {"today_date": today.isoformat(), "today_litres": amount,
               "today_data_source": current.get("data_source", "missing"),
               "today_is_estimated": current.get("is_estimated", False), "yesterday_litres": yesterday,
               "difference_vs_yesterday_litres": _round(diff_yesterday),
               "percentage_vs_yesterday": _round(diff_yesterday / yesterday * 100) if diff_yesterday is not None and yesterday else None,
               "target_litres": current.get("target_litres", target),
               "difference_litres": current.get("difference_litres"),
               "percentage_vs_target": current.get("percentage_vs_target"),
               "seven_day_average_litres": _round(_average(week)),
               "seven_day_observed_days": sum(not p["is_estimated"] for p in week),
               "seven_day_estimated_days": sum(p["is_estimated"] for p in week),
               "month_litres": _round(month_total), "month_kilolitres": _round(month_total / 1000, 3) if month_total is not None else None,
               "month_observed_days": sum(not p["is_estimated"] for p in month),
               "month_estimated_days": sum(p["is_estimated"] for p in month),
               "estimated_cost_rand": _round(month_total / 1000 * tariff) if month_total is not None and tariff > 0 else None,
               "tariff_configured": tariff > 0, "status": current.get("status", "No data"),
               "efficiency_score": score,
               "score_explanation": "Average of min(100, 100 × that day's target ÷ recorded litres) across observed days in the past seven calendar days; zero use scores 100. Estimates and missing days are excluded. This measures target adherence, not plumbing health.",
               "coverage_note": "Totals include only available days; missing days are gaps. Multi-day meter differences are allocated by elapsed time and labelled estimates, never measured daily totals. Manual daily entries take precedence over meter allocations; timestamp readings take precedence over overlapping legacy periods. Partial-day intervals cover only their recorded hours. Cost uses your flat R/kL setting and excludes fixed charges and block tariffs."}
    return {"household": household, "as_of_date": today.isoformat(), "is_simulation": is_simulation,
            "summary": summary, "series": [all_points[day] for day in _days(start_date, end_date)],
            "alerts": [alert for alert in alerts if start_date <= _date(alert["consumption_date"]) <= end_date],
            "daily_reports": [report for report in reports if start_date <= _date(report["date"]) <= end_date]}


def analysis_evidence(dashboard: dict, consumption_date: date) -> dict:
    point = next((p for p in dashboard["series"] if p["date"] == _date(consumption_date).isoformat()), None)
    if point is None or point["litres"] is None:
        raise ValueError("No consumption data is available for the selected day.")
    recent = [p for p in dashboard["series"] if _date(consumption_date) - timedelta(days=29) <= _date(p["date"]) <= _date(consumption_date)]
    return {"date": point["date"], "is_simulation": dashboard["is_simulation"],
            "household_size": dashboard["household"].get("household_size"),
            "facts": {key: point.get(key) for key in ("litres", "target_litres", "difference_litres", "percentage_vs_target",
                        "previous_day_litres", "moving_average_litres", "prior_seven_day_average_litres", "thirty_day_average_litres",
                        "thirty_day_known_days", "data_source", "is_estimated", "coverage_fraction", "flags", "status")},
            "thirty_day_trend": [{key: p.get(key) for key in ("date", "litres", "data_source", "is_estimated", "flags")} for p in recent],
            "limitations": "Unrecorded dates are unknown, not zero. Multi-day allocations are estimates. An anomaly is not proof of a leak. Causes remain unconfirmed; household explanations are self-reported."}


def generate_simulation_records(scenario: str, days: int, target_litres: float, household_size: int,
                                start_date: date, seed: str = "") -> list[dict]:
    if scenario not in SCENARIOS or days not in range(1, 91) or not 1 <= household_size <= 30:
        raise ValueError("Invalid simulation controls.")
    target = _number(target_litres)
    if target <= 0:
        raise ValueError("Simulation target must be positive.")
    rng = random.Random(f"{seed}:{scenario}:{start_date}:{household_size}:{days}")
    records = []
    # Illustrative, not a claim about measured household demand.
    base = min(target * 1.15, max(target * 0.55, household_size * 145))
    for index in range(days):
        day = _date(start_date) + timedelta(days=index)
        amount = base * rng.uniform(0.9, 1.1)
        fraction = index / max(1, days - 1)
        if scenario == "excessive":
            amount = target * rng.uniform(1.3, 1.7)
        elif scenario == "possible_leak" and index >= max(3, days - 3):
            amount = max(base * 2.5, target * 2.2) * rng.uniform(0.98, 1.05)
        elif scenario == "weekend" and day.weekday() >= 5:
            amount *= 1.65
        elif scenario == "gradual_increase":
            amount *= 0.9 + 1.1 * fraction
        elif scenario == "saving":
            amount *= 1.45 - 0.75 * fraction
        else:
            if scenario == "normal":
                amount = min(amount, target * 0.98)
        records.append({"id": str(uuid5(NAMESPACE_URL, f"ecopulse-water:{seed}:{day.isoformat()}")),
                        "date": day.isoformat(), "litres": round(amount, 1), "target_litres": target,
                        "is_simulation": True})
    return records

"""Deterministic electricity accounting, estimates and isolated demonstration data.

Every numerical fact shown by the energy dashboard is calculated here. Meter
values are already kWh; appliance watts alone require division by a thousand.
"""
from __future__ import annotations

import math
import random
import statistics
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo


SCENARIOS = {"normal", "excessive", "geyser", "heater", "weekend", "gradual_increase", "saving"}


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _number(value: Any, default: float = 0) -> float:
    number = float(value) if value is not None else default
    if not math.isfinite(number) or number < 0:
        raise ValueError("Energy values must be finite and non-negative.")
    return number


def _round(value: float | None, digits: int = 2):
    return round(value, digits) if value is not None else None


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


def _average(points: list[dict]) -> float | None:
    values = [point["kwh"] for point in points if point.get("kwh") is not None]
    return statistics.mean(values) if values else None


def _allocate_interval(start: datetime, end: datetime, kwh: float, lower: date,
                       upper: date, zone: ZoneInfo) -> dict[date, dict]:
    seconds = end.timestamp() - start.timestamp()
    if seconds <= 0:
        raise ValueError("Meter readings must have distinct, increasing timestamps.")
    last_day = (end - timedelta(microseconds=1)).date()
    spans_days = start.date() != last_day
    result = {}
    for day in _days(max(start.date(), lower), min(last_day, upper)):
        midnight = datetime.combine(day, time.min, zone)
        following = datetime.combine(day + timedelta(days=1), time.min, zone)
        overlap = max(0, min(end.timestamp(), following.timestamp()) - max(start.timestamp(), midnight.timestamp()))
        if overlap:
            result[day] = {"kwh": kwh * overlap / seconds, "seconds": overlap,
                           "split_estimate": spans_days,
                           "day_seconds": following.timestamp() - midnight.timestamp()}
    return result


def _meter_days(rows: list[dict], lower: date, upper: date, zone: ZoneInfo) -> dict[date, dict]:
    result: dict[date, dict] = {}
    ordered = sorted(rows, key=lambda row: _timestamp(row["reading_at"], zone))
    for row in ordered:
        _number(row["cumulative_kwh"])
    for previous, current in zip(ordered, ordered[1:]):
        start, end = _timestamp(previous["reading_at"], zone), _timestamp(current["reading_at"], zone)
        if start >= end:
            raise ValueError("Meter readings must have distinct, increasing timestamps.")
        if current.get("is_reset"):
            # A changed/reset meter establishes a baseline; the gap stays unknown.
            continue
        delta = _number(current["cumulative_kwh"]) - _number(previous["cumulative_kwh"])
        if delta < -0.0000001:
            raise ValueError("A decreasing cumulative reading requires an explicit meter reset.")
        for day, part in _allocate_interval(start, end, max(0, delta), lower, upper, zone).items():
            point = result.setdefault(day, {"kwh": 0, "seconds": 0, "split_estimate": False,
                                            "day_seconds": part["day_seconds"]})
            point["kwh"] += part["kwh"]
            point["seconds"] += part["seconds"]
            point["split_estimate"] |= part["split_estimate"]
    return result


def _legacy_days(rows: list[dict], lower: date, upper: date, zone: ZoneInfo) -> dict[date, dict]:
    result = {}
    # Historical interval entries are alternate observations, never additive.
    for row in sorted(rows, key=lambda row: str(row.get("created_at", ""))):
        start = datetime.combine(_date(row["previous_date"]), time.min, zone)
        end = datetime.combine(_date(row.get("reading_date", row.get("current_date"))), time.min, zone)
        previous = _number(row.get("previous_value", row.get("previous")))
        current = _number(row.get("current_value", row.get("current")))
        if current < previous:
            raise ValueError("Historical electricity readings contain an invalid decreasing meter.")
        result.update(_allocate_interval(start, end, current - previous, lower, upper, zone))
    return result


def _score(points: list[dict]) -> tuple[int | None, dict]:
    observed = [p for p in points if p["kwh"] is not None and not p["is_estimated"]]
    if not observed:
        return None, {"observed_days": 0, "target_adherence": None, "historical_stability": None}
    adherence = statistics.mean(100 if p["kwh"] == 0 else min(100, 100 * p["target_kwh"] / p["kwh"]) for p in observed)
    values = [p["kwh"] for p in observed]
    stability = None
    if len(values) >= 3:
        average = statistics.mean(values)
        stability = max(0, 100 * (1 - statistics.pstdev(values) / average)) if average else 100
    score = adherence if stability is None else 0.8 * adherence + 0.2 * stability
    return round(score), {"observed_days": len(observed), "target_adherence": _round(adherence),
                          "historical_stability": _round(stability),
                          "target_weight": 1 if stability is None else 0.8,
                          "stability_weight": 0 if stability is None else 0.2}


def build_dashboard(household: dict, daily_records: list[dict], meter_readings: list[dict],
                    legacy_readings: list[dict], *, start_date: date, end_date: date,
                    as_of_date: date | None = None, is_simulation: bool = False) -> dict:
    zone = ZoneInfo(household.get("timezone") or "Africa/Johannesburg")
    today = _date(as_of_date) if as_of_date else datetime.now(zone).date()
    start_date, end_date = _date(start_date), _date(end_date)
    if end_date < start_date or (end_date - start_date).days > 89:
        raise ValueError("Select an ordered date range of at most 90 days.")
    previous_month_end = today.replace(day=1) - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)
    lower = min(start_date - timedelta(days=30), today - timedelta(days=30), previous_month_start)
    upper = max(today, end_date)
    target = _number(household.get("daily_target_kwh"), 15)
    if target <= 0:
        raise ValueError("Daily target must be greater than zero.")
    tariff = _number(household.get("tariff_per_kwh"))
    by_day: dict[date, dict] = {}
    for source, values in (("legacy_meter", _legacy_days(legacy_readings, lower, upper, zone)),
                           ("meter", _meter_days(meter_readings, lower, upper, zone))):
        for day, part in values.items():
            estimated = part["split_estimate"] or part["seconds"] < part["day_seconds"] - 0.01
            by_day[day] = {"id": None, "date": day.isoformat(), "kwh": part["kwh"],
                           "target_kwh": target, "data_source": "estimated" if estimated else "meter",
                           "is_estimated": estimated, "origin": source,
                           "coverage_fraction": min(1, part["seconds"] / part["day_seconds"])}
    for record in daily_records:
        if record.get("is_simulated", record.get("is_simulation", False)) and not is_simulation:
            continue
        day = _date(record.get("date", record.get("consumption_date")))
        if not lower <= day <= upper:
            continue
        # A null amount is unknown, never an implied zero entry.
        value = record.get("kwh", record.get("consumption_kwh"))
        if value is None:
            continue
        goal = _number(record.get("target_kwh", record.get("daily_target_kwh")), target)
        if goal <= 0:
            raise ValueError("Daily target must be greater than zero.")
        by_day[day] = {"id": str(record["id"]) if record.get("id") else None,
                       "date": day.isoformat(), "kwh": _number(value), "target_kwh": goal,
                       "data_source": "simulation" if is_simulation else "manual",
                       "is_estimated": False, "coverage_fraction": 1}
        if is_simulation and record.get("scenario_event"):
            by_day[day]["simulation_scenario_event"] = record["scenario_event"]
    all_points: dict[date, dict] = {}
    alerts, reports = [], []
    for day in _days(lower, upper):
        point = by_day.get(day, {"id": None, "date": day.isoformat(), "kwh": None,
                                 "target_kwh": target, "data_source": "missing", "is_estimated": False,
                                 "coverage_fraction": 0})
        amount, goal = point["kwh"], point["target_kwh"]
        point.update(is_simulation=is_simulation, anomalous=False, flags=[], status="No data", target_status="No data",
                     difference_kwh=None, percentage_vs_target=None, estimated_cost_rand=None)
        prior = [all_points[d] for d in _days(day - timedelta(days=7), day - timedelta(days=1)) if d in all_points]
        prior_values = [p["kwh"] for p in prior if p["kwh"] is not None and not p["is_estimated"]]
        baseline = statistics.mean(prior_values) if prior_values else None
        recent = [all_points[d] for d in _days(day - timedelta(days=6), day - timedelta(days=1)) if d in all_points] + [point]
        month_context = [p for d, p in all_points.items() if day - timedelta(days=29) <= d < day] + [point]
        point.update(moving_average_kwh=_round(_average(recent)),
                     previous_day_kwh=_round(all_points.get(day - timedelta(days=1), {}).get("kwh")),
                     prior_seven_day_average_kwh=_round(baseline),
                     thirty_day_average_kwh=_round(_average(month_context)),
                     thirty_day_known_days=sum(p.get("kwh") is not None for p in month_context))
        if amount is not None:
            diff = amount - goal
            target_status = ("Critical Consumption" if amount > goal * 1.5 else "Above Target" if amount > goal
                             else "Approaching Target" if amount >= goal * 0.9 else "Normal")
            point.update(difference_kwh=_round(diff), percentage_vs_target=_round(diff / goal * 100),
                         estimated_cost_rand=_round(amount * tariff) if tariff > 0 else None,
                         status=target_status, target_status=target_status)
            if diff > 0:
                point["flags"].append("above_target")
            elif amount >= goal * 0.9:
                point["flags"].append("approaching_target")
            if amount > goal * 1.5:
                point["flags"].append("critical_target")
            unusual = (not point["is_estimated"] and len(prior_values) >= 3 and baseline is not None
                       and amount > baseline * 1.5 and amount - baseline >= max(2, goal * 0.2))
            repeat = (not point["is_estimated"] and diff > 0 and all(
                all_points.get(day - timedelta(days=offset), {}).get("kwh") is not None
                and not all_points[day - timedelta(days=offset)]["is_estimated"]
                and all_points[day - timedelta(days=offset)]["kwh"] > all_points[day - timedelta(days=offset)]["target_kwh"]
                for offset in (1, 2)))
            if unusual:
                point["flags"].append("unusual_increase")
                if target_status != "Critical Consumption":
                    point["status"] = "Unusual Consumption"
                if tariff > 0 and (amount - baseline) * tariff >= 5:
                    point["flags"].append("cost_increase")
            if repeat:
                point["flags"].append("repeated_excess")
            if (not point["is_estimated"] and len(prior_values) >= 3 and baseline
                    and amount < baseline * 0.5 and baseline - amount >= max(2, goal * 0.2)):
                point["flags"].append("significant_decrease")
            point["anomalous"] = unusual
            point["additional_cost_rand"] = _round(max(0, diff) * tariff) if tariff > 0 else None
            prefix = "Simulated consumption" if is_simulation else "Estimated allocated consumption" if point["is_estimated"] else "Recorded consumption"
            messages = {
                "above_target": f"{prefix} was {amount:,.2f} kWh on {day.isoformat()}, {diff:,.2f} kWh ({diff / goal * 100:.1f}%) above the {goal:,.2f} kWh target.",
                "approaching_target": f"{prefix} reached {amount / goal * 100:.1f}% of the daily electricity target.",
                "critical_target": "Consumption exceeded one and a half times the configured daily target. Review avoidable use; this is a target threshold, not an electrical safety diagnosis.",
                "unusual_increase": "Recorded electricity use is over one and a half times its recent observed average with a substantial absolute increase. Meter totals alone do not identify the responsible appliance.",
                "repeated_excess": "Recorded electricity consumption exceeded its daily target on three consecutive calendar days.",
                "cost_increase": "Estimated daily electricity cost rose substantially against the recent observed average, using the configured flat tariff.",
                "significant_decrease": "Recorded electricity use fell below half its recent observed average. Check the reading and whether household activity changed.",
            }
            for flag in point["flags"]:
                alerts.append({"consumption_date": day.isoformat(), "alert_type": flag,
                               "severity": "high" if flag == "critical_target" else "info" if flag in {"approaching_target", "significant_decrease"} else "warning",
                               "message": messages[flag], "is_simulation": is_simulation})
            reports.append({key: point[key] for key in ("date", "kwh", "target_kwh", "difference_kwh", "estimated_cost_rand", "status", "is_estimated", "is_simulation")})
        point["kwh"] = _round(amount)
        all_points[day] = point
    current = all_points.get(today, {})
    yesterday = all_points.get(today - timedelta(days=1), {}).get("kwh")
    amount = current.get("kwh")
    week = [p for d, p in all_points.items() if today - timedelta(days=6) <= d <= today and p["kwh"] is not None]
    month = [p for d, p in all_points.items() if today.replace(day=1) <= d <= today and p["kwh"] is not None]
    month_total = sum(p["kwh"] for p in month) if month else None
    score, score_factors = _score(week)
    diff_yesterday = amount - yesterday if amount is not None and yesterday is not None else None
    comparison_days = min(today.day, previous_month_end.day)
    comparable_current = [all_points[today.replace(day=n)] for n in range(1, comparison_days + 1)]
    comparable_previous = [all_points[previous_month_start.replace(day=n)] for n in range(1, comparison_days + 1)]
    comparable = all(p["kwh"] is not None and not p["is_estimated"] for p in comparable_current + comparable_previous)
    current_comparable_total = sum(p["kwh"] for p in comparable_current) if comparable else None
    previous_comparable_total = sum(p["kwh"] for p in comparable_previous) if comparable else None
    month_diff = current_comparable_total - previous_comparable_total if comparable else None
    summary = {"today_date": today.isoformat(), "today_kwh": amount,
               "today_data_source": current.get("data_source", "missing"),
               "today_is_estimated": current.get("is_estimated", False), "yesterday_kwh": yesterday,
               "difference_vs_yesterday_kwh": _round(diff_yesterday),
               "percentage_vs_yesterday": _round(diff_yesterday / yesterday * 100) if diff_yesterday is not None and yesterday else None,
               "target_kwh": current.get("target_kwh", target), "difference_kwh": current.get("difference_kwh"),
               "percentage_vs_target": current.get("percentage_vs_target"),
               "seven_day_average_kwh": _round(_average(week)),
               "seven_day_observed_days": sum(not p["is_estimated"] for p in week),
               "seven_day_estimated_days": sum(p["is_estimated"] for p in week),
               "month_kwh": _round(month_total), "month_observed_days": sum(not p["is_estimated"] for p in month),
               "month_estimated_days": sum(p["is_estimated"] for p in month),
               "month_comparable_kwh": _round(current_comparable_total),
               "previous_month_comparable_kwh": _round(previous_comparable_total),
               "month_comparison_days": comparison_days if comparable else None,
               "month_difference_kwh": _round(month_diff),
               "month_percentage_change": _round(month_diff / previous_comparable_total * 100) if month_diff is not None and previous_comparable_total else None,
               "month_comparison_note": (f"Compared the first {comparison_days} calendar days of each month; both periods have complete observed daily data." if comparable else "No comparison: matching periods need complete observed daily data in both months."),
               "estimated_cost_rand": _round(month_total * tariff) if month_total is not None and tariff > 0 else None,
               "tariff_configured": tariff > 0, "tariff_per_kwh": tariff,
               "status": current.get("status", "No data"), "target_status": current.get("target_status", "No data"),
               "efficiency_score": score, "score_factors": score_factors,
               "score_explanation": "Target adherence is the mean of min(100, 100 x daily target / recorded kWh); zero use scores 100. With at least three observed days, the score weights adherence at 80% and stability at 20%. Stability is max(0, 100 x (1 - population standard deviation / mean)). Otherwise adherence alone is used. Only observed days in the last seven calendar days count. This is a household target and stability indicator, not a certified energy efficiency rating.",
               "coverage_note": "Totals include available days only; missing dates are gaps. Meter intervals spanning days are allocated by elapsed time and labelled estimates. Partial intervals include only covered hours. Manual entries override meter allocations, which override legacy intervals. Costs estimate your flat R/kWh setting and exclude fixed charges, block tariffs and taxes; they are not bills."}
    return {"household": household, "as_of_date": today.isoformat(), "is_simulation": is_simulation,
            "summary": summary, "series": [all_points[day] for day in _days(start_date, end_date)],
            # Canonical recent evidence keeps saved analysis independent of the
            # selected chart filter. The extra context is bounded to 119 days.
            "analysis_history": [all_points[day] for day in _days(start_date - timedelta(days=29), end_date)],
            "alerts": [a for a in alerts if start_date <= _date(a["consumption_date"]) <= end_date],
            "daily_reports": [r for r in reports if start_date <= _date(r["date"]) <= end_date]}


def analysis_evidence(dashboard: dict, consumption_date: date) -> dict:
    point = next((p for p in dashboard["series"] if p["date"] == _date(consumption_date).isoformat()), None)
    if point is None or point["kwh"] is None:
        raise ValueError("No consumption data is available for the selected day.")
    recent = [p for p in dashboard.get("analysis_history", dashboard["series"])
              if _date(consumption_date) - timedelta(days=29) <= _date(p["date"]) <= _date(consumption_date)]
    return {"date": point["date"], "is_simulation": dashboard["is_simulation"],
            "household_size": dashboard["household"].get("household_size"),
            "facts": {key: point.get(key) for key in ("kwh", "target_kwh", "difference_kwh", "percentage_vs_target",
                        "previous_day_kwh", "moving_average_kwh", "prior_seven_day_average_kwh", "thirty_day_average_kwh",
                        "thirty_day_known_days", "estimated_cost_rand", "additional_cost_rand", "data_source", "is_estimated", "coverage_fraction", "flags", "target_status", "status", "simulation_scenario_event")},
            "thirty_day_trend": [{key: p.get(key) for key in ("date", "kwh", "data_source", "is_estimated", "flags")} for p in recent],
            "limitations": "Unknown days are not zero. Allocated meter differences and appliance models are estimates. Consumption does not identify appliance causes; user explanations are self-reported. Flat tariff costs are estimates, not actual bills."}


def generate_simulation_records(scenario: str, days: int, target_kwh: float, household_size: int,
                                start_date: date, seed: str = "") -> list[dict]:
    if scenario not in SCENARIOS or days not in range(1, 91) or not 1 <= household_size <= 30:
        raise ValueError("Invalid simulation controls.")
    target = _number(target_kwh)
    if target <= 0:
        raise ValueError("Simulation target must be positive.")
    rng = random.Random(f"{seed}:{scenario}:{start_date}:{household_size}:{days}")
    records = []
    base = min(target * 0.95, max(target * 0.6, household_size * 3.3))
    for index in range(days):
        day = _date(start_date) + timedelta(days=index)
        amount = base * rng.uniform(0.9, 1.1)
        fraction = index / max(1, days - 1)
        if scenario == "excessive":
            amount = target * rng.uniform(1.3, 1.7)
        elif scenario == "geyser" and index >= max(3, days - 3):
            amount = max(base * 2.2, target * 2.15) * rng.uniform(0.98, 1.05)
        elif scenario == "heater":
            amount += 2 * (2 + 4 * fraction)  # Illustrative 2 kW heater with longer operating time.
        elif scenario == "weekend" and day.weekday() >= 5:
            amount *= 1.65
        elif scenario == "gradual_increase":
            amount *= 0.9 + 1.1 * fraction
        elif scenario == "saving":
            amount *= 1.45 - 0.75 * fraction
        elif scenario == "normal":
            amount = min(amount, target * 0.98)
        records.append({"id": str(uuid5(NAMESPACE_URL, f"ecopulse-energy:{seed}:{day.isoformat()}")),
                        "date": day.isoformat(), "kwh": round(amount, 2), "target_kwh": target,
                        "is_simulation": True})
        if scenario == "heater" or (scenario == "geyser" and index >= max(3, days - 3)):
            records[-1]["scenario_event"] = scenario
    return records


def simulation_spike_record(records: list[dict], target_kwh: float, *, geyser: bool = False, seed: str = "") -> dict:
    """Appendable next-day demonstration spike; never mutates caller/real data."""
    if not records:
        raise ValueError("Generate a simulation before adding a spike.")
    latest = max(records, key=lambda p: _date(p["date"]))
    target = _number(target_kwh)
    if target <= 0:
        raise ValueError("Simulation target must be positive.")
    day = _date(latest["date"]) + timedelta(days=1)
    amount = max(target * (2.15 if geyser else 2), _number(latest["kwh"]) * 1.8)
    return {"id": str(uuid5(NAMESPACE_URL, f"ecopulse-energy:{seed}:{day.isoformat()}")),
            "date": day.isoformat(), "kwh": _round(amount), "target_kwh": target,
            "is_simulation": True, "scenario_event": "geyser" if geyser else "spike"}


def appliance_analysis(appliances: list[dict], tariff_per_kwh: float,
                       days_in_month: int = 30) -> dict:
    tariff = _number(tariff_per_kwh)
    if not 1 <= days_in_month <= 31:
        raise ValueError("Appliance estimate requires one to thirty-one days.")
    modelled = []
    for appliance in appliances:
        watts = _number(appliance.get("power_watts"))
        hours = _number(appliance.get("operating_hours"))
        quantity = _number(appliance.get("quantity"), 1)
        duty = _number(appliance.get("duty_cycle"), 1)
        if hours > 24 or duty > 1 or quantity < 1 or quantity != int(quantity):
            raise ValueError("Operating hours must be at most twenty-four, quantity a positive integer, and duty cycle between zero and one.")
        effective_hours = hours * duty
        daily = watts * effective_hours * quantity / 1000
        name = str(appliance.get("appliance_name", appliance.get("name", "Appliance")))
        # Avoid recommending reduced operation of refrigeration/medical equipment
        # or lowering geyser temperature, where generic advice may be unsafe.
        continuous = any(term in name.lower() for term in ("fridge", "refriger", "freezer", "medical", "oxygen", "geyser", "water heater"))
        reduced_hours = 0 if continuous else min(1, hours)
        saved = watts * reduced_hours * duty * quantity / 1000
        modelled.append({**appliance, "appliance_name": name, "daily_kwh": _round(daily, 4),
                         "monthly_kwh": _round(daily * days_in_month),
                         "daily_cost_rand": _round(daily * tariff) if tariff else None,
                         "monthly_cost_rand": _round(daily * days_in_month * tariff) if tariff else None,
                         "effective_hours": _round(effective_hours, 4), "reduction_hours": reduced_hours,
                         "estimated_saving_kwh_per_day": _round(saved, 4),
                         "estimated_saving_rand_per_day": _round(saved * tariff) if tariff else None,
                         "assumptions": f"Assumes {watts:g} W, {hours:g} scheduled hours per day, duty cycle {duty:g}, quantity {int(quantity)} and {days_in_month} days per month. Operating time and duty cycle are estimates.",
                         "confidence": "Illustrative estimate from user-entered ratings and time; actual draw and cycling can differ.",
                         "is_estimated": True})
    total = sum(p["daily_kwh"] for p in modelled)
    for item in modelled:
        item["percentage_of_modelled_total"] = _round(item["daily_kwh"] / total * 100) if total else 0
    modelled.sort(key=lambda p: p["daily_kwh"], reverse=True)
    recommendations = [{"appliance_id": p.get("id"), "appliance_name": p["appliance_name"],
                        "action": f"If practical, reduce {p['appliance_name']} operating time by {p['reduction_hours']:g} hour per day without compromising health or essential needs.",
                        "energy_reduction_kwh_per_day": p["estimated_saving_kwh_per_day"],
                        "cost_reduction_rand_per_day": p["estimated_saving_rand_per_day"],
                        "assumptions": p["assumptions"] + " Savings assume the same power and duty cycle during the avoided time; recommendations are separate alternatives, not additive guarantees.",
                        "confidence": p["confidence"]}
                       for p in modelled if p["estimated_saving_kwh_per_day"] > 0]
    return {"appliances": modelled, "total_daily_kwh": _round(total),
            "total_monthly_kwh": _round(total * days_in_month),
            "total_daily_cost_rand": _round(total * tariff) if tariff else None,
            "total_monthly_cost_rand": _round(total * days_in_month * tariff) if tariff else None,
            "days_in_month": days_in_month, "recommendations": recommendations,
            "disclaimer": "Appliance figures model entered ratings, operating hours and duty cycle; they do not measure appliances or reconcile automatically with the household meter. Never modify wiring or safety controls; use a qualified electrician for faults."}


def prepaid_summary(purchases: list[dict], balance_observations: list[dict], dashboard: dict,
                    as_of_date: date | None = None) -> dict:
    """Estimate from a dated observed balance, units purchased and covered use.

    Daily totals cannot resolve a balance observed mid-day; those cases require
    another midnight balance or finer readings, rather than subtracting an
    invented fraction. Purchases never establish a starting balance by themselves.
    """
    zone = ZoneInfo(dashboard["household"].get("timezone") or "Africa/Johannesburg")
    today = _date(as_of_date or dashboard["as_of_date"])
    cutoff = datetime.combine(today + timedelta(days=1), time.min, zone)
    month_start = datetime.combine(today.replace(day=1), time.min, zone)
    duration_days = [p for p in dashboard["series"]
                     if today - timedelta(days=6) <= _date(p["date"]) <= today
                     and p.get("kwh") is not None and p.get("coverage_fraction", 0) >= 0.999999]
    # A six-hour interval is not a daily demand rate. Complete multi-day
    # allocations remain usable, provided their uncertainty stays explicit.
    duration_average = _average(duration_days)
    monthly = []
    for purchase in purchases:
        stamp = _purchase_timestamp(purchase["purchase_date"], zone)
        if month_start <= stamp < cutoff:
            monthly.append(purchase)
    amount = sum(_number(p.get("amount_zar")) for p in monthly)
    units = sum(_number(p.get("units_kwh")) for p in monthly)
    result = {"monthly_amount_zar": _round(amount), "monthly_units_kwh": _round(units),
              "average_cost_per_kwh": _round(amount / units, 4) if units else None,
              "estimated_remaining_kwh": None, "estimated_days_remaining": None,
              "starting_balance_kwh": None, "balance_observed_at": None,
              "estimate_as_of": None,
              "duration_average_kwh_per_day": _round(duration_average),
              "duration_average_days": len(duration_days),
              "consumption_since_balance_kwh": None, "purchased_since_balance_kwh": None,
              "is_estimated": True, "note": "Insufficient information to estimate remaining electricity."}
    observations = [b for b in balance_observations if _purchase_timestamp(b.get("observed_at", b.get("observation_date")), zone) < cutoff]
    if not observations:
        return result
    observation = max(observations, key=lambda b: _purchase_timestamp(b.get("observed_at", b.get("observation_date")), zone))
    stamp = _purchase_timestamp(observation.get("observed_at", observation.get("observation_date")), zone)
    balance = _number(observation.get("remaining_kwh", observation.get("known_remaining_kwh")))
    result.update(starting_balance_kwh=balance, balance_observed_at=stamp.isoformat())
    if stamp.time() != time.min:
        if stamp.date() == today:
            # This is the observed snapshot itself; daily totals cannot advance
            # it through the remainder of the day without inventing a profile.
            result.update(estimated_remaining_kwh=_round(balance),
                          estimated_days_remaining=_round(balance / duration_average, 1) if duration_average is not None and duration_average > 0 else None,
                          estimate_as_of=stamp.isoformat(),
                          note="Remaining units are the known balance at the recorded observation time. Estimated days use the recent available daily average; no later consumption or purchases are applied without finer consumption coverage.")
            return result
        result["note"] += " A balance observed during a day cannot be reconciled with whole-day consumption; record a midnight balance or use a newer verified balance."
        return result
    series = {_date(p["date"]): p for p in dashboard["series"]}
    covered = [series.get(day) for day in _days(stamp.date(), today)]
    if any(p is None or p.get("kwh") is None or p.get("coverage_fraction", 0) < 0.999999 for p in covered):
        result["note"] += " Consumption is missing or covers only part of the period since the known balance."
        return result
    consumed = sum(p["kwh"] for p in covered)
    purchased = sum(_number(p.get("units_kwh")) for p in purchases
                    if stamp < _purchase_timestamp(p["purchase_date"], zone) < cutoff)
    remaining = balance + purchased - consumed
    result.update(consumption_since_balance_kwh=_round(consumed), purchased_since_balance_kwh=_round(purchased))
    if remaining < -0.000001:
        result["note"] = "Insufficient information to estimate remaining electricity: recorded consumption exceeds the known balance plus recorded purchases. Check missing purchases, dates and readings."
        return result
    result.update(estimated_remaining_kwh=_round(max(0, remaining)),
                  estimated_days_remaining=_round(max(0, remaining) / duration_average, 1) if duration_average is not None and duration_average > 0 else None,
                  estimate_as_of=cutoff.isoformat(),
                  note="Estimated at the end of the selected day from the observed starting balance plus later units purchased minus available consumption. Allocated meter use and future consumption rates remain uncertain.")
    return result


def _purchase_timestamp(value: Any, zone: ZoneInfo) -> datetime:
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time.min, zone)
    if isinstance(value, str) and len(value) == 10:
        return datetime.combine(_date(value), time.min, zone)
    return _timestamp(value, zone)

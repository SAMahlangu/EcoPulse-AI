"""Transparent weather-hazard screening. Outputs are advisories, never official alerts."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

ENGINE_VERSION = "screening-0.2"
HEAT_MODERATE = float(os.getenv("CLIMATE_HEAT_MODERATE_C", "35"))
HEAT_HIGH = float(os.getenv("CLIMATE_HEAT_HIGH_C", "40"))
HEAT_SEVERE = float(os.getenv("CLIMATE_HEAT_SEVERE_C", "45"))
RAIN_MODERATE = float(os.getenv("CLIMATE_RAIN_MODERATE_MM_24H", "25"))
RAIN_HIGH = float(os.getenv("CLIMATE_RAIN_HIGH_MM_24H", "50"))
RAIN_SEVERE = float(os.getenv("CLIMATE_RAIN_SEVERE_MM_24H", "100"))
WIND_MODERATE = float(os.getenv("CLIMATE_WIND_MODERATE_KMH", "50"))
WIND_HIGH = float(os.getenv("CLIMATE_WIND_HIGH_KMH", "72"))
WIND_SEVERE = float(os.getenv("CLIMATE_WIND_SEVERE_KMH", "90"))


def _level(value: float | None, moderate: float, high: float, severe: float) -> str:
    if value is None: return "unavailable"
    if value >= severe: return "severe"
    if value >= high: return "high"
    if value >= moderate: return "moderate"
    return "low"


def _maximum(rows: list[dict[str, Any]], key: str) -> tuple[float | None, str | None]:
    values=[(float(row[key]),str(row.get("time") or row.get("date") or "")) for row in rows if row.get(key) is not None]
    return max(values, default=(None,None))


def evaluate_forecast(weather: dict[str, Any]) -> dict[str, Any]:
    """Assess 7-day forecast indicators. This is not an impact or flood model."""
    current=weather.get("current") or {}; hourly=weather.get("hourly") or []; daily=weather.get("daily") or []
    max_temp,heat_when=_maximum(daily,"high")
    rain_24,rain_when=_maximum(daily,"precipitation_sum")
    max_wind,wind_when=_maximum(daily,"wind_gusts_max")
    if max_wind is None: max_wind,wind_when=_maximum(hourly,"wind_gusts")

    risks=[
        {"risk":"heat","level":_level(max_temp,HEAT_MODERATE,HEAT_HIGH,HEAT_SEVERE),"score":_score(_level(max_temp,HEAT_MODERATE,HEAT_HIGH,HEAT_SEVERE)),"indicator_name":"maximum forecast air temperature","indicator_value":max_temp,"unit":"°C","expected_period":heat_when,"explanation":f"Maximum forecast temperature is {max_temp}°C. Screening bands are configurable; they are not SAWS heatwave criteria or an official warning.","thresholds":{"moderate":HEAT_MODERATE,"high":HEAT_HIGH,"severe":HEAT_SEVERE},"threshold_basis":"Operational screening bands. SAWS heatwave criteria use the local hottest-month normal plus 5°C for at least three consecutive days; local climatology is not configured here.","precautions":["Limit strenuous outdoor activity during the hottest hours.","Drink water and check on people more vulnerable to heat."]},
        {"risk":"heavy_rain","level":_level(rain_24,RAIN_MODERATE,RAIN_HIGH,RAIN_SEVERE),"score":_score(_level(rain_24,RAIN_MODERATE,RAIN_HIGH,RAIN_SEVERE)),"indicator_name":"largest forecast daily precipitation total","indicator_value":rain_24,"unit":"mm / 24h","expected_period":rain_when,"explanation":f"Largest forecast daily precipitation total is {rain_24} mm. Rainfall alone does not establish flood probability.","thresholds":{"moderate":RAIN_MODERATE,"high":RAIN_HIGH,"severe":RAIN_SEVERE},"threshold_basis":"Screening bands; SAWS/RSMC products show 50 mm and 100 mm per 24-hour precipitation threshold charts. Local impact depends on catchment, drainage, and exposure.","precautions":["Avoid crossing flooded roads, bridges, or flowing water.","Follow official SAWS and municipal warnings."]},
        {"risk":"dry_conditions","level":"unavailable","score":None,"indicator_name":"30-day rainfall departure from 1991–2020 reference","indicator_value":None,"unit":"%","expected_period":None,"explanation":"A dry-condition assessment needs a matched historical rainfall reference. No such comparison is available for this forecast response.","thresholds":None,"threshold_basis":"Requires a documented local climatology baseline; a short forecast is not evidence of drought.","precautions":["Use official water-restriction notices for local guidance."]},
        {"risk":"strong_wind","level":_level(max_wind,WIND_MODERATE,WIND_HIGH,WIND_SEVERE),"score":_score(_level(max_wind,WIND_MODERATE,WIND_HIGH,WIND_SEVERE)),"indicator_name":"maximum forecast wind gust","indicator_value":max_wind,"unit":"km/h","expected_period":wind_when,"explanation":f"Maximum forecast gust is {max_wind} km/h. Screening is not an official warning and does not model local building exposure.","thresholds":{"moderate":WIND_MODERATE,"high":WIND_HIGH,"severe":WIND_SEVERE},"threshold_basis":"Operational lower band is configurable; 72 km/h is cited in SAWS research as a lower bound for hazardous gusts, while vulnerability varies by settlement.","precautions":["Secure loose outdoor items.","Stay clear of trees and temporary structures during strong gusts."]},
    ]
    known=[x for x in risks if x["level"]!="unavailable"]
    overall=max(known,key=lambda x:x["score"] or 0) if known else None
    return {"category":overall["level"] if overall else "unavailable","score":overall["score"] if overall else None,"risks":risks,"explanation":"Weather-hazard screening based on the forecast. It does not include local exposure or vulnerability and is not a comprehensive disaster-risk prediction.","valid_from":weather.get("updated_at"),"valid_until":daily[-1].get("date") if daily else None,"engine_version":ENGINE_VERSION,"source":weather.get("source"),"data_kind":weather.get("data_kind"),"assessed_at":datetime.now(timezone.utc).isoformat()}


def _score(level: str) -> int | None:
    return {"low":10,"moderate":35,"high":65,"severe":90}.get(level)


def assess_dry_conditions(recent_rainfall_mm: float, baseline_rainfall_mm: float, years_of_reference: int) -> dict[str, Any]:
    if years_of_reference < 10 or baseline_rainfall_mm <= 0:
        return {"level":"unavailable","score":None,"rainfall_departure_mm":None,"rainfall_departure_percent":None,"explanation":"At least ten years of nonzero matched historical reference data are needed for this screening."}
    departure=recent_rainfall_mm-baseline_rainfall_mm
    percent=departure/baseline_rainfall_mm*100
    level="severe" if percent<=-75 else "high" if percent<=-50 else "moderate" if percent<=-25 else "low"
    return {"risk":"dry_conditions","level":level,"score":_score(level),"indicator_name":"30-day rainfall departure from 1991–2020 matched windows","indicator_value":round(percent,1),"unit":"%","expected_period":"past 30 complete days","rainfall_departure_mm":round(departure,2),"rainfall_departure_percent":round(percent,1),"recent_rainfall_mm":round(recent_rainfall_mm,2),"reference_rainfall_mm":round(baseline_rainfall_mm,2),"reference_years":years_of_reference,"explanation":"Thirty-day rainfall compared with matched calendar windows in the 1991–2020 reanalysis reference. This is a meteorological dryness indicator, not a declaration of drought.","thresholds":{"moderate":-25,"high":-50,"severe":-75},"threshold_basis":"Configurable screening bands for rainfall departure; not an official drought declaration.","precautions":["Follow official water-restriction notices.","Conserve water where practical."]}

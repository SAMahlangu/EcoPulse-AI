import asyncio
from datetime import date

from fastapi.testclient import TestClient
from backend.app.ai.climate_risk import assess_dry_conditions, evaluate_forecast
from backend.app.main import _aggregate_history, _reference_groups, _with_anomalies, app
from backend.app.services import weather_service


def test_forecast_parser_normalizes_fields_and_directions(monkeypatch):
    raw={
        "latitude":-25.7,"longitude":28.2,"timezone":"Africa/Johannesburg",
        "current":{"time":"2026-10-19T12:00","temperature_2m":30,"apparent_temperature":32,"relative_humidity_2m":25,"precipitation":0,"wind_speed_10m":12,"wind_direction_10m":90,"wind_gusts_10m":20,"cloud_cover":10,"uv_index":8,"weather_code":1},
        "hourly":{"time":["2026-10-19T12:00"],"temperature_2m":[30],"apparent_temperature":[32],"precipitation_probability":[0],"precipitation":[0],"wind_speed_10m":[12],"wind_direction_10m":[90],"wind_gusts_10m":[20],"relative_humidity_2m":[25],"cloud_cover":[10],"uv_index":[8],"weather_code":[1]},
        "daily":{"time":["2026-10-19"],"weather_code":[1],"temperature_2m_max":[35],"temperature_2m_min":[18],"apparent_temperature_max":[37],"apparent_temperature_min":[17],"precipitation_sum":[2],"precipitation_probability_max":[20],"wind_speed_10m_max":[18],"wind_gusts_10m_max":[25]},
    }
    async def fake_request(*args,**kwargs): return raw
    monkeypatch.setattr(weather_service,"_request",fake_request)
    result=asyncio.run(weather_service.forecast(-25.7,28.2))
    assert result["current"]["feels_like"]==32
    assert result["current"]["condition"]=="Mainly clear"
    assert result["hourly"][0]["precipitation_probability"]==0
    assert result["daily"][0]["wind_gusts_max"]==25


def test_historical_parser_preserves_daily_units(monkeypatch):
    raw={"latitude":-25,"longitude":28,"daily":{"time":["2025-01-01","2025-01-02"],"temperature_2m_mean":[20,22],"temperature_2m_max":[25,27],"temperature_2m_min":[15,16],"precipitation_sum":[4,8]}}
    async def fake_request(*args,**kwargs): return raw
    monkeypatch.setattr(weather_service,"_request",fake_request)
    result=asyncio.run(weather_service.history(-25,28,date(2025,1,1),date(2025,1,2)))
    assert result["daily"][1]["temperature_mean"]==22
    assert result["daily"][0]["precipitation_sum"]==4
    assert "reanalysis" in result["data_kind"]


def test_municipality_geocoding_matches_province_and_municipality(monkeypatch):
    async def fake_search(name):
        assert name in {"City of Tshwane Metropolitan Municipality", "Tshwane"}
        return [
            {"name": "Pretoria", "admin1": "Eastern Cape", "admin2": "Joe Gqabi District Municipality",
             "admin3": "Elundini", "latitude": -30.78, "longitude": 28.59},
            {"name": "Pretoria", "admin1": "Gauteng", "admin2": "City of Tshwane Metropolitan Municipality",
             "admin3": "City of Tshwane", "latitude": -25.74486, "longitude": 28.18783},
        ]

    monkeypatch.setattr(weather_service, "search_locations", fake_search)
    result = asyncio.run(weather_service.municipality_coordinates(
        "City of Tshwane Metropolitan Municipality", "Gauteng",
    ))

    assert result == {"latitude": -25.74486, "longitude": 28.18783}


def test_municipality_geocoding_does_not_return_other_municipality(monkeypatch):
    async def fake_search(_name):
        return [{"name": "Pretoria", "admin1": "Gauteng", "admin2": "City of Tshwane Metropolitan Municipality",
                 "admin3": "City of Tshwane", "latitude": -25.74486, "longitude": 28.18783}]

    monkeypatch.setattr(weather_service, "search_locations", fake_search)
    result = asyncio.run(weather_service.municipality_coordinates("City of Johannesburg", "Gauteng"))

    assert result is None


def test_risk_engine_classifies_high_heat_and_heavy_rain():
    forecast={"updated_at":"2026-10-19T12:00","source":"Open-Meteo","data_kind":"forecast","current":{},"hourly":[],"daily":[{"date":"2026-10-20","high":41,"precipitation_sum":55,"wind_gusts_max":75}]}
    result=evaluate_forecast(forecast)
    risks={item["risk"]:item for item in result["risks"]}
    assert risks["heat"]["level"]=="high"
    assert risks["heavy_rain"]["level"]=="high"
    assert risks["strong_wind"]["level"]=="high"
    assert risks["dry_conditions"]["level"]=="unavailable"
    assert result["engine_version"]


def test_dry_assessment_needs_reference_sample_and_calculates_anomaly():
    assert assess_dry_conditions(5,100,8)["level"]=="unavailable"
    result=assess_dry_conditions(50,100,30)
    assert result["rainfall_departure_mm"]==-50
    assert result["rainfall_departure_percent"]==-50
    assert result["level"]=="high"


def test_historical_aggregation_sums_rain_and_averages_temperature():
    days=[{"date":"2025-01-01","temperature_mean":20,"temperature_max":25,"temperature_min":15,"precipitation_sum":5},{"date":"2025-01-02","temperature_mean":24,"temperature_max":29,"temperature_min":18,"precipitation_sum":7}]
    monthly=_aggregate_history(days,"monthly")[0]
    assert monthly["temperature_mean"]==22
    assert monthly["temperature_max"]==29
    assert monthly["temperature_min"]==15
    assert monthly["precipitation_sum"]==12


def test_anomaly_uses_matched_month_reference_and_rain_percent():
    selected=[{"date":"2025-01","temperature_mean":22,"temperature_max":30,"temperature_min":10,"precipitation_sum":120}]
    reference={"01":{"date":"01","reference_years":30,"temperature_mean":20,"temperature_max":27,"temperature_min":8,"precipitation_sum":100}}
    anomaly=_with_anomalies(selected,reference,"monthly")[0]
    assert anomaly["temperature_anomaly_c"]==2
    assert anomaly["rainfall_anomaly_mm"]==20
    assert anomaly["rainfall_anomaly_percent"]==20


def test_annual_reference_is_one_long_term_mean():
    rows=[]
    for year in range(1991,2021):
        rows.append({"date":f"{year}-01-01","temperature_mean":20,"temperature_max":30,"temperature_min":10,"precipitation_sum":800})
    reference=_reference_groups(rows,"annual")
    assert reference["annual"]["precipitation_sum"]==800
    assert reference["annual"]["reference_years"]==30


def test_analytics_endpoint_returns_monthly_rainfall_and_temperature_anomalies(tmp_path,monkeypatch):
    monkeypatch.setattr("backend.app.main.DB_PATH",tmp_path/"climate-api.sqlite3")
    async def fake_history(latitude,longitude,start_date,end_date):
        if start_date.year==1991:
            rows=[]
            for year in range(1991,2021):
                rows.extend([
                    {"date":f"{year}-01-01","temperature_mean":20,"temperature_max":25,"temperature_min":15,"precipitation_sum":5},
                    {"date":f"{year}-01-02","temperature_mean":20,"temperature_max":25,"temperature_min":15,"precipitation_sum":5},
                ])
        else:
            rows=[{"date":"2025-01-01","temperature_mean":22,"temperature_max":27,"temperature_min":18,"precipitation_sum":15},{"date":"2025-01-02","temperature_mean":22,"temperature_max":27,"temperature_min":18,"precipitation_sum":15}]
        return {"location":{"latitude":latitude,"longitude":longitude},"daily":rows,"source":"Mock archive","data_kind":"mock reanalysis","updated_at":"2025-01-03T00:00:00Z"}
    monkeypatch.setattr(weather_service,"history",fake_history)
    with TestClient(app) as client:
        response=client.get("/api/v1/climate/analytics",params={"start_date":"2025-01-01","end_date":"2025-01-02","aggregation":"monthly"})
    assert response.status_code==200
    row=response.json()["series"][0]
    assert row["precipitation_sum"]==30
    assert row["rainfall_anomaly_mm"]==20
    assert row["rainfall_anomaly_percent"]==200
    assert row["temperature_anomaly_c"]==2

import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.services import weather_service


@pytest.fixture
def climate_client(tmp_path, monkeypatch):
    monkeypatch.setattr(main,"DB_PATH",tmp_path/"climate.sqlite3")
    async def fake_forecast(latitude,longitude,**kwargs):
        return {"location":{"latitude":latitude,"longitude":longitude,"timezone":"Africa/Johannesburg"},"current":{"temperature":41,"feels_like":42,"humidity":20,"precipitation":0,"wind_speed":15,"wind_direction":90,"wind_gusts":18,"cloud_cover":10,"uv_index":8,"weather_code":0,"condition":"Clear"},"hourly":[{"time":"2026-10-20T12:00","temperature":40,"feels_like":41,"precipitation_probability":0,"precipitation":0,"wind_speed":15,"wind_direction":90,"wind_gusts":18,"humidity":20,"cloud_cover":10,"uv_index":8,"weather_code":0}],"daily":[{"date":"2026-10-20","weather_code":0,"high":41,"low":20,"feels_like_high":42,"feels_like_low":19,"precipitation_sum":0,"precipitation_probability_max":0,"wind_speed_max":20,"wind_gusts_max":25}],"updated_at":"2026-10-19T12:00","source":"Mock Open-Meteo","data_kind":"mock forecast","units":{}}
    monkeypatch.setattr(weather_service,"forecast",fake_forecast)
    async def dry(*args): return {"risk":"dry_conditions","level":"unavailable","score":None,"indicator_name":"matched historical rainfall","indicator_value":None,"unit":"%","expected_period":None,"explanation":"Reference data unavailable.","threshold_basis":"Requires reference data.","precautions":[]}
    monkeypatch.setattr(main,"dry_condition_risk",dry)
    with TestClient(main.app) as client:
        yield client


def test_risk_endpoint_returns_four_transparent_hazards(climate_client):
    result=climate_client.get("/api/v1/climate/risks").json()
    assert result["engine_version"]
    assert {item["risk"] for item in result["risks"]}=={"heat","heavy_rain","dry_conditions","strong_wind"}
    heat=next(x for x in result["risks"] if x["risk"]=="heat")
    assert heat["level"]=="high"
    assert "not SAWS heatwave criteria" in heat["explanation"]


def test_generated_advisory_is_not_returned_as_official(climate_client):
    result=climate_client.get("/api/v1/climate/advisories").json()
    assert result["official_warnings"]["available"] is False
    assert all(item["is_official"] is False for item in result["ecopulse_advisories"]["items"])
    assert all("not official" in item["verification_status"] for item in result["ecopulse_advisories"]["items"])


def test_climate_map_returns_only_real_coordinate_marker(climate_client):
    result=climate_client.get("/api/v1/climate/map?latitude=-25.7&longitude=28.2&layer=temperature").json()
    assert len(result["markers"])==1
    assert result["markers"][0]["latitude"]==-25.7
    assert result["incidents"]==[]
    assert "no spatial interpolation" in result["note"].lower()


def test_advisor_answers_from_the_requested_daily_forecast(climate_client, monkeypatch):
    import httpx
    import json
    from backend.app.services import climate_advisor
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    def gemini(method, url, **kwargs):
        context = json.loads(kwargs['json']['contents'][0]['parts'][0]['text'])
        assert context['question'] == 'Can I swim this Sunday?'
        assert context['weather']['daily'][0]['high'] == 41
        assert context['weather']['location']['latitude'] == -30
        assert context['current_date_south_africa']
        return httpx.Response(200, request=httpx.Request('POST', url), json={'candidates':[{'content':{'parts':[{'text':'The supplied forecast does not cover Sunday.'}]}}]})
    monkeypatch.setattr(climate_advisor.network, 'request', gemini)
    response=climate_client.post('/api/v1/climate/advisor',json={'question':'Can I swim this Sunday?', 'latitude':-30, 'longitude':25})
    assert response.status_code == 200
    assert response.json()['answer'] == 'The supplied forecast does not cover Sunday.'
    assert 'Gemini' in response.json()['method']
    assert response.json()['source'] == 'Mock Open-Meteo'


def test_history_rejects_future_end_date(climate_client):
    response=climate_client.get("/api/v1/climate/history",params={"start_date":"2025-01-01","end_date":"2099-01-01"})
    assert response.status_code==422

from datetime import date, timedelta
import json

import httpx
import pytest

from backend.app.services.water_intelligence import build_dashboard, analysis_evidence, generate_simulation_records
from backend.app.services import water_advisor


TODAY = date(2026, 10, 25)
HOUSEHOLD = {"id": "household", "name": "Home", "household_size": 4,
             "daily_target_litres": 600, "tariff_per_kl": 25, "timezone": "Africa/Johannesburg"}


def dashboard(records=None, meters=None, legacy=None, **kwargs):
    return build_dashboard(HOUSEHOLD, records or [], meters or [], legacy or [],
                           start_date=kwargs.pop("start_date", TODAY - timedelta(days=6)),
                           end_date=kwargs.pop("end_date", TODAY), as_of_date=TODAY, **kwargs)


def point(data, day):
    return next(row for row in data["series"] if row["date"] == day)


def test_percentage_target_month_cost_and_yesterday_are_calculated_in_python():
    result = dashboard([{"date": "2026-10-24", "litres": 590}, {"date": "2026-10-25", "litres": 850}])
    summary = result["summary"]
    assert summary["difference_litres"] == 250
    assert summary["percentage_vs_target"] == 41.67
    assert summary["difference_vs_yesterday_litres"] == 260
    assert summary["month_litres"] == 1440
    assert summary["month_kilolitres"] == 1.44
    assert summary["estimated_cost_rand"] == 36
    assert summary["seven_day_average_litres"] == 720
    assert summary["seven_day_observed_days"] == 2


def test_missing_dates_are_null_and_actual_zero_is_recorded():
    result = dashboard([{"date": "2026-10-25", "litres": 0}])
    assert point(result, "2026-10-24")["litres"] is None
    assert point(result, "2026-10-25")["litres"] == 0
    assert result["summary"]["yesterday_litres"] is None
    assert result["summary"]["efficiency_score"] == 100
    assert len(result["daily_reports"]) == 1
    empty = dashboard()
    assert empty["summary"]["month_litres"] is None
    assert empty["summary"]["efficiency_score"] is None
    assert empty["daily_reports"] == []


def test_exact_midnight_interval_measures_previous_day_and_first_reading_is_baseline():
    meters = [{"reading_at": "2026-10-24T00:00:00+02:00", "cumulative_kl": 10},
              {"reading_at": "2026-10-25T00:00:00+02:00", "cumulative_kl": 10.6}]
    result = dashboard(meters=meters)
    measured = point(result, "2026-10-24")
    assert measured["litres"] == 600
    assert measured["data_source"] == "meter"
    assert not measured["is_estimated"]
    assert point(result, "2026-10-25")["litres"] is None
    assert dashboard(meters=meters[:1])["summary"]["month_litres"] is None


def test_multi_day_meter_interval_allocates_estimates_without_inventing_daily_measurements():
    meters = [{"reading_at": "2026-10-22T00:00:00+02:00", "cumulative_kl": 10},
              {"reading_at": "2026-10-25T00:00:00+02:00", "cumulative_kl": 12.7}]
    result = dashboard(meters=meters)
    for day in ("2026-10-22", "2026-10-23", "2026-10-24"):
        allocated = point(result, day)
        assert allocated["litres"] == 900
        assert allocated["data_source"] == "estimated"
        assert allocated["is_estimated"]
        assert not allocated["anomalous"]
    assert result["summary"]["month_estimated_days"] == 3
    assert result["summary"]["efficiency_score"] is None


def test_partial_day_records_are_not_presented_as_full_measured_days():
    result = dashboard(meters=[{"reading_at": "2026-10-25T06:00:00+02:00", "cumulative_kl": 10},
                               {"reading_at": "2026-10-25T12:00:00+02:00", "cumulative_kl": 10.15}])
    current = point(result, "2026-10-25")
    assert current["litres"] == 150
    assert current["is_estimated"]
    assert current["coverage_fraction"] == 0.25
    assert result["summary"]["today_is_estimated"]


def test_complete_same_day_subintervals_can_make_a_measured_daily_total():
    result = dashboard(meters=[{"reading_at": "2026-10-24T00:00:00+02:00", "cumulative_kl": 10},
                               {"reading_at": "2026-10-24T12:00:00+02:00", "cumulative_kl": 10.2},
                               {"reading_at": "2026-10-25T00:00:00+02:00", "cumulative_kl": 10.6}])
    assert point(result, "2026-10-24")["litres"] == 600
    assert not point(result, "2026-10-24")["is_estimated"]


def test_reset_creates_gap_and_new_baseline_then_measures_new_meter():
    meters = [{"reading_at": "2026-10-22T00:00:00+02:00", "cumulative_kl": 998},
              {"reading_at": "2026-10-23T00:00:00+02:00", "cumulative_kl": 0.1, "is_reset": True},
              {"reading_at": "2026-10-24T00:00:00+02:00", "cumulative_kl": 0.6}]
    result = dashboard(meters=meters)
    assert point(result, "2026-10-22")["litres"] is None
    assert point(result, "2026-10-23")["litres"] == 500
    meters[1]["is_reset"] = False
    with pytest.raises(ValueError, match="reset"):
        dashboard(meters=meters)


def test_manual_record_overrides_meter_and_overlap_does_not_double_count():
    legacy = [{"previous_date": "2026-10-23", "reading_date": "2026-10-25", "previous_value": 0, "current_value": 2, "created_at": "a"},
              {"previous_date": "2026-10-23", "reading_date": "2026-10-25", "previous_value": 0, "current_value": 1.2, "created_at": "b"}]
    result = dashboard([{"date": "2026-10-24", "litres": 400}], legacy=legacy)
    assert point(result, "2026-10-23")["litres"] == 600
    assert point(result, "2026-10-24")["litres"] == 400
    assert result["summary"]["month_litres"] == 1000


def test_month_boundary_counts_only_allocated_current_month_volume():
    result = dashboard(legacy=[{"previous_date": "2026-09-30", "reading_date": "2026-10-02",
                                "previous_value": 1, "current_value": 2}], start_date=date(2026, 10, 1))
    assert result["summary"]["month_litres"] == 500
    assert result["summary"]["month_estimated_days"] == 1


def test_observed_anomaly_and_possible_leak_are_qualified_screening_not_diagnosis():
    rows = [{"date": (TODAY - timedelta(days=n)).isoformat(), "litres": 550} for n in range(1, 7)]
    rows.append({"date": TODAY.isoformat(), "litres": 1400})
    result = dashboard(rows)
    latest = result["series"][-1]
    assert latest["anomalous"]
    assert "possible_leak" in latest["flags"]
    alert = next(a for a in result["alerts"] if a["alert_type"] == "possible_leak")
    assert "does not confirm" in alert["message"]
    assert result["summary"]["efficiency_score"] == 92


def test_repeated_excess_requires_consecutive_calendar_days_not_just_records():
    rows = [{"date": "2026-10-20", "litres": 900}, {"date": "2026-10-23", "litres": 900}, {"date": "2026-10-25", "litres": 900}]
    assert "repeated_excess" not in dashboard(rows)["series"][-1]["flags"]
    rows += [{"date": "2026-10-24", "litres": 900}]
    assert "repeated_excess" in dashboard(rows)["series"][-1]["flags"]


def test_graph_filter_does_not_change_current_month_summary():
    rows = [{"date": "2026-10-01", "litres": 400}, {"date": "2026-10-25", "litres": 600}]
    result = dashboard(rows, start_date=date(2026, 10, 1), end_date=date(2026, 10, 7))
    assert result["summary"]["today_litres"] == 600
    assert result["summary"]["month_litres"] == 1000
    assert len(result["series"]) == 7


@pytest.mark.parametrize("scenario", ["normal", "excessive", "possible_leak", "weekend", "gradual_increase", "saving"])
def test_all_simulation_scenarios_produce_separate_deterministic_daily_records(scenario):
    rows = generate_simulation_records(scenario, 30, 600, 4, date(2026, 9, 26), "session")
    assert rows == generate_simulation_records(scenario, 30, 600, 4, date(2026, 9, 26), "session")
    assert len(rows) == 30
    assert len({row["id"] for row in rows}) == 30
    assert all(row["is_simulation"] and row["litres"] >= 0 for row in rows)
    result = build_dashboard(HOUSEHOLD, rows, [], [], start_date=date(2026, 9, 26), end_date=TODAY,
                             as_of_date=TODAY, is_simulation=True)
    assert all(row["data_source"] == "simulation" for row in result["series"])
    assert result["summary"]["today_litres"] == rows[-1]["litres"]


def test_evidence_uses_actual_facts_and_rejects_unknown_days():
    result = dashboard([{"date": "2026-10-25", "litres": 850}])
    evidence = analysis_evidence(result, TODAY)
    assert evidence["facts"]["percentage_vs_target"] == 41.67
    assert evidence["facts"]["previous_day_litres"] is None
    with pytest.raises(ValueError):
        analysis_evidence(result, date(2026, 10, 24))


@pytest.fixture
def evidence():
    return analysis_evidence(dashboard([{"date": "2026-10-25", "litres": 850}]), TODAY)


def test_gemini_not_configured_returns_contextual_rules(evidence, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = water_advisor.analyse_water(evidence, {"reasons": ["visitors", "laundry"], "text": "Extra washing"})
    assert result["source"] == "rules"
    assert "visitors" in result["analysis"]["possible_causes"][0]
    assert "does not establish" in result["analysis"]["possible_causes"][0]


def test_structured_provider_output_is_validated_and_has_no_generated_numbers(evidence, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-not-a-real-key")
    captured = {}
    payload = {"summary": "Consumption is above the configured target.", "possible_causes": ["Additional laundry may have contributed."],
               "recommendations": ["Monitor the next recorded day for a return to your usual pattern."],
               "caveat": "A household explanation cannot verify how much water each activity used."}
    def request(method, url, **kwargs):
        captured.update(kwargs)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}, request=httpx.Request(method, url))
    monkeypatch.setattr(water_advisor.network, "request", request)
    result = water_advisor.analyse_water(evidence)
    assert result["source"] == "gemini"
    assert captured["json"]["generationConfig"]["responseMimeType"] == "application/json"
    sent = json.loads(captured["json"]["contents"][0]["parts"][0]["text"])
    assert sent["evidence"]["facts"]["litres"] == 850
    payload["summary"] = "You consumed 9999 litres and have a confirmed leak."
    assert water_advisor.analyse_water(evidence)["source"] == "rules"


@pytest.mark.parametrize("failure", ["timeout", "malformed", 403, 429, 500])
def test_provider_failures_return_useful_rules_without_leaking_private_details(evidence, monkeypatch, caplog, failure):
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-not-a-real-key")
    def request(method, url, **kwargs):
        if failure == "timeout":
            raise httpx.TimeoutException("private household text test-secret-not-a-real-key")
        if failure == "malformed":
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "not JSON"}]}}]}, request=httpx.Request(method, url))
        return httpx.Response(failure, text="private household text", request=httpx.Request(method, url))
    monkeypatch.setattr(water_advisor.network, "request", request)
    result = water_advisor.analyse_water(evidence)
    assert result["source"] == "rules"
    assert result["analysis"]["recommendations"]
    assert "private household text" not in caplog.text
    assert "test-secret-not-a-real-key" not in caplog.text

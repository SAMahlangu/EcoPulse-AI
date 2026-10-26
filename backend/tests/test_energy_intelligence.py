from datetime import date, timedelta
import json

import httpx
import pytest

from backend.app.services.energy_intelligence import (
    SCENARIOS, analysis_evidence, appliance_analysis, build_dashboard,
    generate_simulation_records, prepaid_summary, simulation_spike_record,
)
from backend.app.services import energy_advisor


TODAY = date(2026, 10, 25)
HOUSEHOLD = {"id": "household", "name": "Home", "household_size": 4,
             "daily_target_kwh": 15, "tariff_per_kwh": 2.75, "timezone": "Africa/Johannesburg"}


def dashboard(records=None, meters=None, legacy=None, household=None, **kwargs):
    return build_dashboard(household or HOUSEHOLD, records or [], meters or [], legacy or [],
                           start_date=kwargs.pop("start_date", TODAY - timedelta(days=6)),
                           end_date=kwargs.pop("end_date", TODAY), as_of_date=TODAY, **kwargs)


def point(data, day):
    return next(row for row in data["series"] if row["date"] == day)


def daily_range(start, days, amount):
    return [{"date": (start + timedelta(days=n)).isoformat(), "kwh": amount} for n in range(days)]


def test_target_yesterday_month_and_flat_tariff_costs_use_kwh_without_water_conversion():
    result = dashboard([{"date": "2026-10-24", "kwh": 14}, {"date": "2026-10-25", "kwh": 22}])
    summary = result["summary"]
    assert summary["difference_kwh"] == 7
    assert summary["percentage_vs_target"] == 46.67
    assert summary["difference_vs_yesterday_kwh"] == 8
    assert summary["percentage_vs_yesterday"] == 57.14
    assert summary["month_kwh"] == 36
    assert summary["estimated_cost_rand"] == 99
    assert summary["seven_day_average_kwh"] == 18
    assert summary["seven_day_observed_days"] == 2
    assert result["daily_reports"][-1]["estimated_cost_rand"] == 60.5
    assert result["series"][-1]["additional_cost_rand"] == 19.25


def test_missing_and_null_are_unknown_zero_is_recorded_and_no_missing_notifications():
    result = dashboard([{"date": "2026-10-24", "kwh": None}, {"date": "2026-10-25", "kwh": 0}])
    assert point(result, "2026-10-24")["kwh"] is None
    assert point(result, "2026-10-25")["kwh"] == 0
    assert result["summary"]["yesterday_kwh"] is None
    assert result["summary"]["efficiency_score"] == 100
    assert len(result["daily_reports"]) == 1
    assert result["alerts"] == []
    empty = dashboard()
    assert empty["summary"]["month_kwh"] is None
    assert empty["summary"]["estimated_cost_rand"] is None
    assert empty["summary"]["efficiency_score"] is None
    assert empty["daily_reports"] == []


def test_unconfigured_tariff_is_unknown_cost_and_zero_yesterday_has_no_division_percentage():
    result = dashboard([{"date": "2026-10-24", "kwh": 0}, {"date": "2026-10-25", "kwh": 5}],
                       household={**HOUSEHOLD, "tariff_per_kwh": 0})
    assert result["summary"]["percentage_vs_yesterday"] is None
    assert result["summary"]["estimated_cost_rand"] is None
    assert result["daily_reports"][-1]["estimated_cost_rand"] is None


@pytest.mark.parametrize("value,status", [(13.49, "Normal"), (13.5, "Approaching Target"),
                                         (15, "Approaching Target"), (15.01, "Above Target"),
                                         (22.5, "Above Target"), (22.51, "Critical Consumption")])
def test_target_threshold_boundaries_are_independent_of_historical_anomaly(value, status):
    current = dashboard([{"date": TODAY.isoformat(), "kwh": value}])["series"][-1]
    assert current["target_status"] == status
    assert not current["anomalous"]
    assert "unusual_increase" not in current["flags"]


def test_electricity_meter_difference_is_kwh_and_midnight_belongs_to_previous_day():
    meters = [{"reading_at": "2026-10-24T00:00:00+02:00", "cumulative_kwh": 1250},
              {"reading_at": "2026-10-25T00:00:00+02:00", "cumulative_kwh": 1268}]
    result = dashboard(meters=meters)
    measured = point(result, "2026-10-24")
    assert measured["kwh"] == 18
    assert measured["data_source"] == "meter"
    assert not measured["is_estimated"]
    assert point(result, "2026-10-25")["kwh"] is None
    assert dashboard(meters=meters[:1])["summary"]["month_kwh"] is None


def test_multi_day_intervals_are_estimates_and_excluded_from_score_and_anomaly_screen():
    meters = [{"reading_at": "2026-10-22T00:00:00+02:00", "cumulative_kwh": 1250},
              {"reading_at": "2026-10-25T00:00:00+02:00", "cumulative_kwh": 1304}]
    result = dashboard(meters=meters)
    for day in ("2026-10-22", "2026-10-23", "2026-10-24"):
        allocated = point(result, day)
        assert allocated["kwh"] == 18
        assert allocated["data_source"] == "estimated"
        assert allocated["is_estimated"]
        assert not allocated["anomalous"]
    assert result["summary"]["month_estimated_days"] == 3
    assert result["summary"]["efficiency_score"] is None


def test_partial_day_is_labelled_and_complete_subintervals_can_make_full_observed_day():
    partial = dashboard(meters=[{"reading_at": "2026-10-25T06:00:00+02:00", "cumulative_kwh": 1250},
                                {"reading_at": "2026-10-25T12:00:00+02:00", "cumulative_kwh": 1254.5}])
    current = point(partial, "2026-10-25")
    assert current["kwh"] == 4.5
    assert current["is_estimated"]
    assert current["coverage_fraction"] == 0.25
    full = dashboard(meters=[{"reading_at": "2026-10-24T00:00:00+02:00", "cumulative_kwh": 1250},
                             {"reading_at": "2026-10-24T12:00:00+02:00", "cumulative_kwh": 1255},
                             {"reading_at": "2026-10-25T00:00:00+02:00", "cumulative_kwh": 1268}])
    assert point(full, "2026-10-24")["kwh"] == 18
    assert not point(full, "2026-10-24")["is_estimated"]


def test_reset_creates_unknown_gap_and_new_baseline_before_next_measurement():
    meters = [{"reading_at": "2026-10-22T00:00:00+02:00", "cumulative_kwh": 9998},
              {"reading_at": "2026-10-23T00:00:00+02:00", "cumulative_kwh": 1, "is_reset": True},
              {"reading_at": "2026-10-24T00:00:00+02:00", "cumulative_kwh": 18}]
    result = dashboard(meters=meters)
    assert point(result, "2026-10-22")["kwh"] is None
    assert point(result, "2026-10-23")["kwh"] == 17
    meters[1]["is_reset"] = False
    with pytest.raises(ValueError, match="reset"):
        dashboard(meters=meters)


@pytest.mark.parametrize("meters", [
    [{"reading_at": "2026-10-25T12:00:00", "cumulative_kwh": 12}],
    [{"reading_at": "2026-10-25T12:00:00+02:00", "cumulative_kwh": 12},
     {"reading_at": "2026-10-25T12:00:00+02:00", "cumulative_kwh": 13}],
])
def test_ambiguous_or_duplicate_meter_timestamps_are_rejected(meters):
    with pytest.raises(ValueError):
        dashboard(meters=meters)


def test_manual_overrides_meter_and_newest_overlapping_legacy_interval_does_not_double_count():
    legacy = [{"previous_date": "2026-10-23", "reading_date": "2026-10-25", "previous_value": 0, "current_value": 60, "created_at": "a"},
              {"previous_date": "2026-10-23", "reading_date": "2026-10-25", "previous_value": 0, "current_value": 36, "created_at": "b"}]
    result = dashboard([{"date": "2026-10-24", "kwh": 14}], legacy=legacy)
    assert point(result, "2026-10-23")["kwh"] == 18
    assert point(result, "2026-10-24")["kwh"] == 14
    assert result["summary"]["month_kwh"] == 32


def test_month_boundary_allocates_only_current_month_kwh_and_graph_filter_does_not_change_summary():
    result = dashboard(legacy=[{"previous_date": "2026-09-30", "reading_date": "2026-10-02",
                                "previous_value": 1250, "current_value": 1280}],
                       start_date=date(2026, 10, 1), end_date=date(2026, 10, 7))
    assert result["summary"]["month_kwh"] == 15
    assert result["summary"]["month_estimated_days"] == 1
    assert len(result["series"]) == 7
    filtered = dashboard([{"date": "2026-10-01", "kwh": 14}, {"date": "2026-10-25", "kwh": 16}],
                         start_date=date(2026, 10, 1), end_date=date(2026, 10, 7))
    assert filtered["summary"]["today_kwh"] == 16
    assert filtered["summary"]["month_kwh"] == 30


def test_month_comparison_requires_complete_matched_observed_calendar_days():
    rows = daily_range(date(2026, 9, 1), 25, 10) + daily_range(date(2026, 10, 1), 25, 12)
    result = dashboard(rows)
    assert result["summary"]["previous_month_comparable_kwh"] == 250
    assert result["summary"]["month_comparable_kwh"] == 300
    assert result["summary"]["month_difference_kwh"] == 50
    assert result["summary"]["month_percentage_change"] == 20
    assert result["summary"]["month_comparison_days"] == 25
    rows.pop(0)
    assert dashboard(rows)["summary"]["month_difference_kwh"] is None


def test_anomaly_can_be_below_target_and_critical_threshold_can_have_stable_history():
    rows = daily_range(TODAY - timedelta(days=6), 6, 5) + [{"date": TODAY.isoformat(), "kwh": 12}]
    latest = dashboard(rows)["series"][-1]
    assert latest["anomalous"]
    assert latest["status"] == "Unusual Consumption"
    assert latest["target_status"] == "Normal"
    assert "above_target" not in latest["flags"]
    stable_high = daily_range(TODAY - timedelta(days=6), 7, 25)
    latest = dashboard(stable_high)["series"][-1]
    assert latest["status"] == "Critical Consumption"
    assert not latest["anomalous"]
    assert "repeated_excess" in latest["flags"]


def test_consecutive_alert_requires_calendar_continuity_and_cost_increase_needs_baseline():
    rows = [{"date": "2026-10-20", "kwh": 18}, {"date": "2026-10-23", "kwh": 18}, {"date": "2026-10-25", "kwh": 25}]
    assert "repeated_excess" not in dashboard(rows)["series"][-1]["flags"]
    rows += [{"date": "2026-10-24", "kwh": 18}]
    assert "repeated_excess" in dashboard(rows)["series"][-1]["flags"]
    rows[-2]["kwh"] = 40
    latest = dashboard(rows)["series"][-1]
    assert "cost_increase" in latest["flags"]
    assert len({(a["consumption_date"], a["alert_type"]) for a in dashboard(rows)["alerts"]}) == len(dashboard(rows)["alerts"])


def test_score_is_transparent_and_stability_contributes_only_with_sufficient_history():
    stable = dashboard(daily_range(TODAY - timedelta(days=6), 7, 15))["summary"]
    assert stable["efficiency_score"] == 100
    assert stable["score_factors"]["historical_stability"] == 100
    variable = dashboard([{ "date": (TODAY - timedelta(days=n)).isoformat(), "kwh": 0 if n % 2 else 15} for n in range(7)])["summary"]
    assert variable["score_factors"]["target_adherence"] == 100
    assert variable["efficiency_score"] < 100
    assert "not a certified" in variable["score_explanation"]
    short = dashboard([{"date": TODAY.isoformat(), "kwh": 30}])["summary"]
    assert short["efficiency_score"] == 50
    assert short["score_factors"]["stability_weight"] == 0


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_all_seven_simulations_are_deterministic_and_separate_from_actual_data(scenario):
    rows = generate_simulation_records(scenario, 30, 15, 4, date(2026, 9, 26), "session")
    assert rows == generate_simulation_records(scenario, 30, 15, 4, date(2026, 9, 26), "session")
    assert len(rows) == 30
    assert len({row["id"] for row in rows}) == 30
    assert all(row["is_simulation"] and row["kwh"] >= 0 for row in rows)
    result = dashboard(rows, start_date=date(2026, 9, 26), is_simulation=True)
    assert all(row["data_source"] == "simulation" for row in result["series"])
    assert result["summary"]["today_kwh"] == rows[-1]["kwh"]
    assert dashboard(rows)["summary"]["month_kwh"] is None


@pytest.mark.parametrize("geyser", [False, True])
def test_spike_and_geyser_events_append_without_mutating_base_records(geyser):
    rows = generate_simulation_records("normal", 7, 15, 4, TODAY - timedelta(days=7), "session")
    original = json.dumps(rows)
    event = simulation_spike_record(rows, 15, geyser=geyser, seed="session")
    assert json.dumps(rows) == original
    assert event["date"] == TODAY.isoformat()
    assert event["is_simulation"]
    assert event["kwh"] >= 30
    result = dashboard(rows + [event], is_simulation=True)
    assert result["series"][-1]["anomalous"]
    assert "critical_target" in result["series"][-1]["flags"]
    assert result["series"][-1]["additional_cost_rand"] > 0
    assert all(a["is_simulation"] for a in result["alerts"])
    evidence = analysis_evidence(result, TODAY)
    assert evidence["facts"]["simulation_scenario_event"] == ("geyser" if geyser else "spike")


def test_simulation_geyser_cause_is_explicitly_designated_and_never_verified_for_actual_data(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    rows = generate_simulation_records("geyser", 7, 15, 4, TODAY - timedelta(days=6), "session")
    evidence = analysis_evidence(dashboard(rows, is_simulation=True), TODAY)
    result = energy_advisor.analyse_energy(evidence)
    assert "designates extended geyser" in result["analysis"]["possible_causes"][0]
    actual = dashboard([{"date": TODAY.isoformat(), "kwh": 32, "scenario_event": "geyser"}])
    evidence = analysis_evidence(actual, TODAY)
    assert evidence["facts"]["simulation_scenario_event"] is None


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_invalid_consumption_and_tariffs_are_rejected(value):
    with pytest.raises(ValueError):
        dashboard([{"date": TODAY.isoformat(), "kwh": value}])
    with pytest.raises(ValueError):
        dashboard(household={**HOUSEHOLD, "tariff_per_kwh": value})


def test_appliance_watts_hours_quantity_duty_and_savings_are_python_calculations():
    result = appliance_analysis([
        {"id": "heater", "appliance_name": "Electric heater", "power_watts": 2000, "operating_hours": 5, "quantity": 1, "duty_cycle": 1},
        {"id": "fridge", "appliance_name": "Refrigerator", "power_watts": 200, "operating_hours": 24, "quantity": 1, "duty_cycle": 0.25},
    ], 2.75)
    heater, fridge = result["appliances"]
    assert heater["daily_kwh"] == 10
    assert heater["monthly_kwh"] == 300
    assert heater["daily_cost_rand"] == 27.5
    assert heater["percentage_of_modelled_total"] == 89.29
    assert fridge["daily_kwh"] == 1.2
    assert fridge["effective_hours"] == 6
    assert fridge["reduction_hours"] == 0
    assert result["total_daily_kwh"] == 11.2
    recommendation = result["recommendations"][0]
    assert recommendation["energy_reduction_kwh_per_day"] == 2
    assert recommendation["cost_reduction_rand_per_day"] == 5.5
    assert "duty cycle" in recommendation["assumptions"]
    assert "do not measure" in result["disclaimer"]


def test_appliance_models_do_not_recommend_disabling_essential_or_unsafe_loads():
    result = appliance_analysis([
        {"appliance_name": name, "power_watts": 1000, "operating_hours": 5, "quantity": 1}
        for name in ("Electric geyser", "Medical oxygen machine", "Refrigerator", "Freezer")
    ], 2.75)
    assert result["recommendations"] == []
    assert all(a["is_estimated"] for a in result["appliances"])


@pytest.mark.parametrize("change", [{"operating_hours": 25}, {"duty_cycle": 1.5}, {"quantity": 0}, {"quantity": 1.5}])
def test_invalid_appliance_inputs_are_rejected(change):
    with pytest.raises(ValueError):
        appliance_analysis([{"appliance_name": "Heater", "power_watts": 2000, "operating_hours": 5, "quantity": 1, **change}], 2.75)


def test_prepaid_requires_known_balance_not_rand_or_purchased_units_alone():
    data = dashboard(daily_range(TODAY - timedelta(days=2), 3, 15))
    purchases = [{"purchase_date": "2026-10-24", "amount_zar": 150, "units_kwh": 50}]
    result = prepaid_summary(purchases, [], data)
    assert result["monthly_amount_zar"] == 150
    assert result["monthly_units_kwh"] == 50
    assert result["average_cost_per_kwh"] == 3
    assert result["estimated_remaining_kwh"] is None
    assert "Insufficient information" in result["note"]


def test_prepaid_reconciles_balance_later_units_and_complete_consumption():
    data = dashboard(daily_range(TODAY - timedelta(days=2), 3, 15))
    purchases = [{"purchase_date": "2026-10-24T10:00:00+02:00", "amount_zar": 150, "units_kwh": 50}]
    result = prepaid_summary(purchases, [{"observed_at": "2026-10-23", "remaining_kwh": 40}], data)
    assert result["estimated_remaining_kwh"] == 45
    assert result["estimated_days_remaining"] == 3
    assert result["consumption_since_balance_kwh"] == 45
    assert result["purchased_since_balance_kwh"] == 50
    assert result["is_estimated"]


def test_prepaid_missing_or_partial_consumption_and_inconsistent_negative_balance_are_unknown():
    observation = [{"observed_at": "2026-10-23", "remaining_kwh": 40}]
    missing = prepaid_summary([], observation, dashboard([{"date": TODAY.isoformat(), "kwh": 15}]))
    assert missing["estimated_remaining_kwh"] is None
    data = dashboard(daily_range(TODAY - timedelta(days=2), 3, 15))
    negative = prepaid_summary([], observation, data)
    assert negative["estimated_remaining_kwh"] is None
    assert "exceeds" in negative["note"]
    midday = prepaid_summary([], [{"observed_at": "2026-10-24T12:00:00+02:00", "remaining_kwh": 45}], data)
    assert midday["estimated_remaining_kwh"] is None
    assert "during a day" in midday["note"]


def test_prepaid_same_day_observation_shows_known_snapshot_and_days_at_observation_time():
    data = dashboard(daily_range(TODAY - timedelta(days=2), 3, 15))
    observed = "2026-10-25T12:00:00+02:00"
    result = prepaid_summary([{ "purchase_date": "2026-10-25T14:00:00+02:00", "amount_zar": 150, "units_kwh": 50}],
                            [{"observed_at": observed, "remaining_kwh": 45}], data)
    assert result["estimated_remaining_kwh"] == 45
    assert result["estimated_days_remaining"] == 3
    assert result["estimate_as_of"] == observed
    assert result["consumption_since_balance_kwh"] is None
    assert result["purchased_since_balance_kwh"] is None


def test_prepaid_days_do_not_treat_partial_day_allocations_as_daily_demand():
    data = dashboard(meters=[{"reading_at": "2026-10-25T06:00:00+02:00", "cumulative_kwh": 1250},
                             {"reading_at": "2026-10-25T12:00:00+02:00", "cumulative_kwh": 1254.5}])
    result = prepaid_summary([], [{"observed_at": "2026-10-25T12:00:00+02:00", "remaining_kwh": 45}], data)
    assert result["estimated_remaining_kwh"] == 45
    assert result["estimated_days_remaining"] is None
    assert result["duration_average_days"] == 0


def test_analysis_evidence_contains_only_available_date_facts():
    evidence = analysis_evidence(dashboard([{"date": TODAY.isoformat(), "kwh": 22}]), TODAY)
    assert evidence["facts"]["percentage_vs_target"] == 46.67
    assert evidence["facts"]["previous_day_kwh"] is None
    with pytest.raises(ValueError):
        analysis_evidence(dashboard(), TODAY)


def test_analysis_evidence_is_canonical_across_chart_filters_for_the_same_selected_day():
    rows = daily_range(date(2026, 9, 1), 55, 12)
    full = dashboard(rows, start_date=TODAY - timedelta(days=29))
    short = dashboard(rows, start_date=TODAY - timedelta(days=6))
    selected_only = dashboard(rows, start_date=TODAY, end_date=TODAY)
    assert analysis_evidence(full, TODAY) == analysis_evidence(short, TODAY)
    assert analysis_evidence(full, TODAY) == analysis_evidence(selected_only, TODAY)
    assert len(analysis_evidence(short, TODAY)["thirty_day_trend"]) == 30
    longest = dashboard(rows, start_date=TODAY - timedelta(days=89))
    assert len(longest["analysis_history"]) == 119


@pytest.fixture
def evidence():
    return analysis_evidence(dashboard([{"date": TODAY.isoformat(), "kwh": 22}]), TODAY)


def test_missing_gemini_key_uses_contextual_unverified_explanation_rules(evidence, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = energy_advisor.analyse_energy(evidence, {"reasons": ["geyser", "visitors"], "text": "Hot water for visitors"})
    assert result["source"] == "rules"
    assert result["provider_status"] == "not_configured"
    assert "visitors" in result["analysis"]["possible_causes"][0]
    assert "does not establish" in result["analysis"]["possible_causes"][0]
    assert "qualified electrician" in " ".join(result["analysis"]["recommendations"])
    text_only = energy_advisor.analyse_energy(evidence, {"reasons": [], "text": "Extended work"})
    assert "not been independently verified" in text_only["analysis"]["possible_causes"][0]


def provider_response(payload):
    def request(method, url, **kwargs):
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}, request=httpx.Request(method, url))
    return request


def test_gemini_receives_calculated_facts_and_validated_structured_prose(evidence, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-not-a-real-key")
    captured = {}
    payload = {"summary": "Consumption is above your configured target.", "possible_causes": ["Extended heater use may have contributed."],
               "recommendations": ["Review unnecessary appliance operating time while maintaining essential needs."],
               "caveat": "The meter total cannot identify the responsible appliance."}
    def request(method, url, **kwargs):
        captured.update(kwargs)
        return provider_response(payload)(method, url, **kwargs)
    monkeypatch.setattr(energy_advisor.network, "request", request)
    result = energy_advisor.analyse_energy(evidence)
    assert result["source"] == "gemini"
    sent = json.loads(captured["json"]["contents"][0]["parts"][0]["text"])
    assert sent["evidence"]["facts"]["kwh"] == 22
    assert captured["json"]["generationConfig"]["responseMimeType"] == "application/json"
    assert captured["json"]["generationConfig"]["responseJsonSchema"]["additionalProperties"] is False


@pytest.mark.parametrize("summary", ["You can save 999 kWh daily.", "You can save two kilowatt hours daily.",
    "Your geyser caused the increase.", "Bypass the safety breaker to reduce consumption.",
    "Reduce the geyser temperature to save energy.", "<a href='malicious'>Click here</a>"])
def test_model_numbers_definitive_causes_unsafe_advice_and_markup_fall_back(evidence, monkeypatch, summary):
    monkeypatch.setenv("GEMINI_API_KEY", "test-not-a-real-key")
    payload = {"summary": summary, "possible_causes": [], "recommendations": ["Review the next observed reading."], "caveat": "The cause remains unknown."}
    monkeypatch.setattr(energy_advisor.network, "request", provider_response(payload))
    assert energy_advisor.analyse_energy(evidence)["source"] == "rules"


@pytest.mark.parametrize("failure", ["timeout", "malformed", 403, 429, 500])
def test_provider_failures_fall_back_without_logging_secret_or_household_text(evidence, monkeypatch, caplog, failure):
    monkeypatch.setenv("GEMINI_API_KEY", "test-not-a-real-key")
    def request(method, url, **kwargs):
        if failure == "timeout":
            raise httpx.TimeoutException("private household text test-not-a-real-key")
        if failure == "malformed":
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "not JSON"}]}}]}, request=httpx.Request(method, url))
        return httpx.Response(failure, text="private household text", request=httpx.Request(method, url))
    monkeypatch.setattr(energy_advisor.network, "request", request)
    result = energy_advisor.analyse_energy(evidence)
    assert result["source"] == "rules"
    assert result["analysis"]["recommendations"]
    assert "private household text" not in caplog.text
    assert "test-not-a-real-key" not in caplog.text

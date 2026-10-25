export interface EnergyHousehold {
  id: string; name: string; household_size: number; daily_target_kwh: number;
  tariff_per_kwh: number | null; timezone: string;
}
export interface EnergyDay {
  id: string | null; date: string; kwh: number | null; target_kwh: number;
  moving_average_kwh: number | null; data_source: string; is_estimated: boolean;
  is_simulation: boolean; anomalous: boolean; flags: string[];
  difference_kwh: number | null; percentage_vs_target: number | null;
  estimated_cost_rand: number | null; status: string; target_status: string;
  additional_cost_rand?: number | null;
}
export interface EnergyAlert {
  id: string; alert_type: string; severity: string; message: string;
  consumption_date: string; status: 'unread' | 'read' | 'dismissed' | 'resolved';
}
export interface EnergySummary {
  today_date: string; today_kwh: number | null; today_data_source: string;
  today_is_estimated: boolean; yesterday_kwh: number | null;
  difference_vs_yesterday_kwh: number | null; percentage_vs_yesterday: number | null;
  target_kwh: number; difference_kwh: number | null; percentage_vs_target: number | null;
  seven_day_average_kwh: number | null; seven_day_observed_days: number; seven_day_estimated_days: number;
  month_kwh: number | null; month_observed_days: number; month_estimated_days: number;
  previous_month_comparable_kwh?: number | null; month_difference_kwh?: number | null;
  month_percentage_change?: number | null; month_comparison_days?: number | null; month_comparison_note?: string;
  estimated_cost_rand: number | null;
  tariff_configured: boolean; status: string; efficiency_score: number | null;
  score_explanation: string; score_factors?: Record<string, unknown> | string[]; coverage_note: string;
}
export interface EnergyReport {
  date: string; kwh: number; target_kwh: number; difference_kwh: number;
  estimated_cost_rand: number | null; status: string; is_estimated: boolean; is_simulation: boolean;
}
export interface EnergyDashboard {
  household: EnergyHousehold; as_of_date: string; is_simulation: boolean; session_id?: string;
  tariff_assumption?: string;
  summary: EnergySummary; series: EnergyDay[]; alerts: EnergyAlert[]; daily_reports: EnergyReport[];
}
export interface EnergyInsight {
  source: 'gemini' | 'rules'; model: string; generated_at: string;
  analysis: { summary: string; possible_causes: string[]; recommendations: string[]; caveat: string };
}
export interface EnergyExplanation { reasons: string[]; text: string }
export interface EnergyInsightsResponse { items: EnergyInsight[]; explanation: EnergyExplanation | null }
export interface EnergyMeterReading { id: string; reading_at: string; cumulative_kwh: number; is_reset: boolean; notes?: string }
export interface EnergyAppliance {
  id: string; appliance_name: string; power_watts: number | null; operating_hours: number | null; quantity: number; duty_cycle: number;
  daily_kwh: number | null; monthly_kwh: number | null; daily_cost_rand: number | null; monthly_cost_rand: number | null;
  percentage_of_modelled_total: number | null; effective_hours?: number; assumptions: string; confidence: string; incomplete?: boolean;
}
export interface EnergyRecommendation {
  appliance_id?: string; appliance_name?: string; action: string; energy_reduction_kwh_per_day: number | null;
  cost_reduction_rand_per_day: number | null; assumptions: string; confidence: string;
}
export interface EnergyApplianceAnalysis {
  appliances: EnergyAppliance[]; total_daily_kwh: number; total_monthly_kwh: number;
  total_daily_cost_rand: number | null; total_monthly_cost_rand: number | null;
  recommendations: EnergyRecommendation[]; disclaimer: string; days_in_month: number;
}
export interface EnergyAdvisorResponse { recommendations: EnergyRecommendation[]; insight?: EnergyInsight; disclaimer?: string }
export interface EnergyPrepaid {
  purchases: { id: string; purchase_date: string; amount_zar: number; units_kwh: number }[];
  balances: { id: string; observed_at: string; remaining_kwh: number }[];
  summary: { monthly_amount_zar: number; monthly_units_kwh: number; average_cost_per_kwh: number | null;
    estimated_remaining_kwh: number | null; estimated_days_remaining: number | null;
    starting_balance_kwh: number | null; balance_observed_at: string | null; estimate_as_of: string | null;
    consumption_since_balance_kwh: number | null; purchased_since_balance_kwh: number | null;
    is_estimated: boolean; note: string };
}
export const energyReasons = [
  ['geyser_usage', 'Geyser usage'], ['electric_heating', 'Electric heating'], ['air_conditioning', 'Air conditioning'],
  ['laundry', 'Laundry'], ['cooking', 'Cooking'], ['visitors', 'Visitors'],
  ['working_from_home', 'Working from home'], ['charging_devices', 'Charging electronic devices'],
  ['additional_appliances', 'Additional appliance usage'], ['other', 'Other'],
] as const;
export const energyNumber = (value: number | null | undefined, digits = 1) => value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('en-ZA', { maximumFractionDigits: digits });
export const energyMoney = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('en-ZA', { style: 'currency', currency: 'ZAR' });
export const energyDate = (date: string) => new Date(`${date.slice(0, 10)}T12:00:00`).toLocaleDateString('en-ZA', { day: 'numeric', month: 'short', year: 'numeric' });
export const energyStatus = (status: string) => status.replace(/_/g, ' ').replace(/\b\w/g, value => value.toUpperCase());

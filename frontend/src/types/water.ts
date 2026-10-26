export interface WaterHousehold {
  id: string;
  name: string;
  household_size: number;
  daily_target_litres: number;
  tariff_per_kl: number | null;
  timezone: string;
}

export interface WaterDay {
  id: string | null;
  date: string;
  litres: number | null;
  target_litres: number;
  moving_average_litres: number | null;
  data_source: 'manual' | 'meter' | 'estimated' | 'missing' | 'simulation';
  is_estimated: boolean;
  is_simulation: boolean;
  anomalous: boolean;
  flags: string[];
  difference_litres: number | null;
  percentage_vs_target: number | null;
}

export interface WaterAlert {
  id: string;
  alert_type: string;
  severity: string;
  message: string;
  consumption_date: string;
  status: 'unread' | 'read' | 'dismissed' | 'resolved';
}

export interface WaterSummary {
  today_date: string;
  today_litres: number | null;
  today_data_source: string;
  today_is_estimated: boolean;
  yesterday_litres: number | null;
  difference_vs_yesterday_litres: number | null;
  percentage_vs_yesterday: number | null;
  target_litres: number;
  difference_litres: number | null;
  percentage_vs_target: number | null;
  seven_day_average_litres: number | null;
  seven_day_observed_days: number;
  seven_day_estimated_days: number;
  month_litres: number | null;
  month_kilolitres: number | null;
  month_observed_days: number;
  month_estimated_days: number;
  estimated_cost_rand: number | null;
  tariff_configured: boolean;
  status: string;
  efficiency_score: number | null;
  score_explanation: string;
  coverage_note: string;
}

export interface WaterDashboard {
  household: WaterHousehold;
  as_of_date: string;
  is_simulation: boolean;
  session_id?: string;
  summary: WaterSummary;
  series: WaterDay[];
  alerts: WaterAlert[];
  daily_reports: { date: string; litres: number; target_litres: number; difference_litres: number; status: string; is_estimated: boolean; is_simulation: boolean }[];
}

export interface WaterInsight {
  source: 'gemini' | 'rules';
  model: string;
  analysis: { summary: string; possible_causes: string[]; recommendations: string[]; caveat: string };
  generated_at: string;
}

export interface WaterExplanation { reasons: string[]; text: string }
export interface WaterInsightsResponse { items: WaterInsight[]; explanation: WaterExplanation | null }
export interface WaterMeterReading { id: string; reading_at: string; cumulative_kl: number; is_reset: boolean; notes?: string }

export const waterReasons = [
  ['laundry', 'Laundry'], ['visitors', 'Visitors'], ['gardening', 'Gardening'],
  ['washing_vehicles', 'Washing vehicles'], ['household_cleaning', 'Household cleaning'],
  ['swimming_pool', 'Filling a swimming pool'], ['suspected_leak', 'Suspected plumbing leak'],
  ['maintenance', 'Maintenance activities'], ['other', 'Other'],
] as const;

export const waterNumber = (value: number | null | undefined, digits = 0) => value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('en-ZA', { maximumFractionDigits: digits });
export const waterDate = (date: string) => new Date(`${date.slice(0, 10)}T12:00:00`).toLocaleDateString('en-ZA', { day: 'numeric', month: 'short', year: 'numeric' });
export const waterStatus = (status: string) => status.replace(/_/g, ' ').replace(/\b\w/g, value => value.toUpperCase());

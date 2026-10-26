"""Owner-scoped household energy records and isolated, persistent hackathon simulations."""
from __future__ import annotations

from contextlib import contextmanager
from collections import defaultdict, deque
from calendar import monthrange
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import os
from threading import Lock
import time as clock
from typing import Any, Literal
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .security import postgres_connection, require_roles
from .services.energy_intelligence import (analysis_evidence, build_dashboard, generate_simulation_records,
    appliance_analysis, prepaid_summary, simulation_spike_record)
from .services.energy_advisor import analyse_energy

router = APIRouter(prefix="/api/v1/energy", tags=["Energy consumption intelligence"])
# Every route requires a verified active resident; ownership always comes from this actor, not client-supplied IDs.
resident = require_roles("resident")
MAX_ROWS = 2000
MAX_SIMULATION_DAYS = 90
_analysis_attempts: dict[str, deque] = defaultdict(deque)
_analysis_attempts_lock = Lock()
Scenario = Literal["normal", "excessive", "geyser", "heater", "weekend", "gradual_increase", "saving"]
Reason = Literal["geyser_usage", "electric_heating", "air_conditioning", "laundry", "cooking", "visitors",
                 "working_from_home", "charging_devices", "additional_appliances", "other"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class HouseholdInput(Input):
    name: str = Field(default="My household", min_length=1, max_length=80)
    household_size: int = Field(default=4, ge=1, le=30)
    daily_target_kwh: float = Field(default=15, gt=0, le=10000000)
    tariff_per_kwh: float = Field(default=0, ge=0, le=100000)
    timezone: str = Field(default="Africa/Johannesburg", max_length=80)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Enter a household name.")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Choose a supported IANA timezone.") from exc
        return value


class ConsumptionInput(Input):
    date: date
    kwh: float = Field(ge=0, le=10000000)


class MeterInput(Input):
    reading_at: datetime
    cumulative_kwh: float = Field(ge=0, le=1000000000)
    is_reset: bool = False
    notes: str = Field(default="", max_length=1000)

    @field_validator("reading_at")
    @classmethod
    def aware_timestamp(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Include the reading timezone or UTC offset.")
        return value


class AnalysisInput(Input):
    date: date
    session_id: UUID | None = None


class ExplanationInput(AnalysisInput):
    reasons: list[Reason] = Field(default_factory=list, max_length=10)
    text: str = Field(default="", max_length=2000)

    @field_validator("reasons")
    @classmethod
    def unique_reasons(cls, value):
        return list(dict.fromkeys(value))


class AlertInput(Input):
    status: Literal["read", "dismissed"]


class SimulationInput(Input):
    household_size: int = Field(default=4, ge=1, le=30)
    days: Literal[7, 14, 30] = 30
    daily_target_kwh: float = Field(default=15, gt=0, le=10000000)
    tariff_per_kwh: float = Field(default=2.75, ge=0, le=100000)
    scenario: Scenario = "normal"


class SessionInput(Input):
    session_id: UUID


class ApplianceInput(Input):
    appliance_name: str = Field(min_length=1, max_length=100)
    power_watts: float = Field(gt=0, le=1000000)
    operating_hours: float = Field(ge=0, le=24)
    quantity: int = Field(default=1, ge=1, le=100)
    duty_cycle: float = Field(default=1, ge=0, le=1)

    @field_validator("appliance_name")
    @classmethod
    def trimmed_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Enter an appliance name.")
        return value


class PrepaidPurchaseInput(Input):
    purchase_date: datetime
    amount_zar: float = Field(gt=0, le=10000000)
    units_kwh: float = Field(gt=0, le=10000000)

    @field_validator("purchase_date")
    @classmethod
    def aware_timestamp(cls, value):
        return MeterInput.aware_timestamp(value)


class PrepaidBalanceInput(Input):
    observed_at: datetime
    remaining_kwh: float = Field(ge=0, le=10000000)

    @field_validator("observed_at")
    @classmethod
    def aware_timestamp(cls, value):
        return MeterInput.aware_timestamp(value)


def validation_error_response():
    # FastAPI's default error body echoes inputs, including non-JSON NaN/Infinity.
    return JSONResponse({"detail": "Check the energy record's dates, values and required fields."}, status_code=422)


def _json(value):
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"), allow_nan=False)


@contextmanager
def _transaction():
    try:
        with postgres_connection() as connection:
            connection.execute("set local statement_timeout = '12s'")
            yield connection
    except HTTPException:
        raise
    except Exception as exc:
        if getattr(exc, "sqlstate", None) == "23505":
            raise HTTPException(409, "This energy record already exists. Edit the saved record instead.") from exc
        raise HTTPException(503, "Energy intelligence data is unavailable. Check the database connection and migration 015.") from exc


HOUSEHOLD_COLUMNS = "h.id::text,h.owner_id::text,h.name,h.household_size,h.daily_target_kwh,h.tariff_per_kwh,h.timezone,h.created_at,h.updated_at"


def _household(connection, user, *, lock=False, provision=True):
    """Resolve the authenticated resident's household; request payloads cannot select another household."""
    sql = f"""select {HOUSEHOLD_COLUMNS} from public.energy_households h
        join public.profiles p on p.id=h.owner_id
        where h.owner_id=%s and p.role='resident' and coalesce(p.account_status,'active')='active'"""
    row = connection.execute(sql + (" for update of h" if lock else ""), (user["id"],)).fetchone()
    if row is None and not provision:
        profile = connection.execute("""select id::text,household_size from public.profiles
            where id=%s and role='resident' and coalesce(account_status,'active')='active'""", (user["id"],)).fetchone()
        if profile is None:
            raise HTTPException(403, "An active resident household is required.")
        # Simulations may start before actual-data setup; defaults stay in the session.
        return {"id": str(uuid4()), "owner_id": str(profile["id"]), "name": "Simulated household",
                "household_size": min(30, max(1, int(profile.get("household_size") or 4))),
                "daily_target_kwh": 15, "tariff_per_kwh": 0, "timezone": "Africa/Johannesburg"}
    if row is None:
        connection.execute("""insert into public.energy_households(owner_id,household_size)
            select id,coalesce(household_size,4) from public.profiles
            where id=%s and role='resident' and coalesce(account_status,'active')='active'
            on conflict(owner_id) do nothing""", (user["id"],))
        row = connection.execute(sql + (" for update of h" if lock else ""), (user["id"],)).fetchone()
    if row is None:
        raise HTTPException(403, "An active resident household is required.")
    return dict(row)


def _today(household):
    return datetime.now(ZoneInfo(household["timezone"])).date()


def _actual_date(value, household):
    if value > _today(household):
        raise HTTPException(422, "Actual energy records cannot be dated in the future.")
    if value < date(2000, 1, 1):
        raise HTTPException(422, "Choose a consumption date on or after 1 January 2000.")
    if value < _today(household) - timedelta(days=366):
        raise HTTPException(422, "Record actual consumption within the past 366 days.")


def _range(today, days, start, end):
    end = end or today
    start = start or end - timedelta(days=days - 1)
    if end > today or start > end or (end - start).days >= 90:
        raise HTTPException(422, "Choose an ordered date range of at most 90 days, ending no later than today.")
    if start < date(2000, 1, 1):
        raise HTTPException(422, "Choose a date range on or after 1 January 2000.")
    if start < today - timedelta(days=366):
        raise HTTPException(422, "Choose a date range within the past 366 days.")
    return start, end


def _bounded(rows):
    if len(rows) > MAX_ROWS:
        raise HTTPException(422, "This date range contains too many energy readings. Choose a shorter range.")
    return [dict(row) for row in rows]


def _actual_dashboard(connection, household, *, days=30, start=None, end=None, persist=False):
    today = _today(household)
    start, end = _range(today, days, start, end)
    # Continuous context avoids bridging over omitted meter readings. The API caps
    # lookback so custom historical ranges cannot trigger an unbounded scan.
    previous_month_start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    chart_start = min(start - timedelta(days=30), today - timedelta(days=30), previous_month_start)
    household_id = household["id"]
    daily = _bounded(connection.execute("""select id::text,date,kwh,target_kwh,data_source,is_simulation,created_at,updated_at
        from public.energy_consumption_daily where household_id=%s and
        date between %s and %s order by date limit %s""",
        (household_id, chart_start, today, MAX_ROWS + 1)).fetchall())
    windows = [(chart_start, today)]
    meters_by_id = {}
    zone = ZoneInfo(household["timezone"])
    for first, last in windows:
        first_at = datetime.combine(first, time.min, zone)
        last_at = datetime.combine(last + timedelta(days=1), time.min, zone)
        rows = connection.execute("""select id::text,reading_at,cumulative_kwh,is_reset,notes from public.energy_meter_readings
            where household_id=%s and reading_at between %s and %s order by reading_at limit %s""",
            (household_id, first_at, last_at, MAX_ROWS + 1)).fetchall()
        before = connection.execute("""select id::text,reading_at,cumulative_kwh,is_reset,notes from public.energy_meter_readings
            where household_id=%s and reading_at<%s order by reading_at desc limit 1""", (household_id, first_at)).fetchone()
        after = connection.execute("""select id::text,reading_at,cumulative_kwh,is_reset,notes from public.energy_meter_readings
            where household_id=%s and reading_at>%s order by reading_at limit 1""", (household_id, last_at)).fetchone()
        for row in _bounded(rows) + ([dict(before)] if before else []) + ([dict(after)] if after else []):
            meters_by_id[row["id"]] = row
    meters = sorted(meters_by_id.values(), key=lambda row: row["reading_at"])
    legacy = _bounded(connection.execute("""select id::text,previous_value,current_value,previous_date,reading_date,created_at
        from public.energy_readings where resident_id=%s and
        reading_date >= %s and previous_date <= %s
        order by previous_date,reading_date limit %s""",
        (household["owner_id"], chart_start, today, MAX_ROWS + 1)).fetchall())
    dashboard = build_dashboard(household, daily, meters, legacy, start_date=start, end_date=end, as_of_date=today)
    if persist:
        _persist_reports(connection, household, dashboard)
    dashboard["alerts"] = [dict(row) for row in connection.execute("""select id::text,consumption_date,alert_type,severity,message,status,created_at
        from public.energy_alerts where household_id=%s and consumption_date between %s and %s
        order by consumption_date desc,created_at desc limit 100""", (household_id, start, end)).fetchall()]
    dashboard["explanations"] = [_explanation_row(row) for row in connection.execute("""select id::text,consumption_date,reasons,text,updated_at
        from public.energy_consumption_explanations where household_id=%s and consumption_date between %s and %s
        order by consumption_date desc limit 90""", (household_id, start, end)).fetchall()]
    latest = connection.execute("""select id::text,consumption_date,source,model,analysis,generated_at,expires_at,input_hash
        from public.energy_ai_insights where household_id=%s and consumption_date between %s and %s
        and expires_at>now()
        order by consumption_date desc,generated_at desc limit 1""", (household_id, start, end)).fetchone()
    dashboard["latest_insight"] = None
    if latest and any(str(point["date"]) == str(latest["consumption_date"]) and point.get("kwh") is not None for point in dashboard["series"]):
        explanation = next((item for item in dashboard["explanations"] if str(item["date"]) == str(latest["consumption_date"])), None)
        evidence = _analysis_context(connection, household, dashboard, latest["consumption_date"])
        if _fingerprint(evidence, explanation) == latest["input_hash"]:
            dashboard["latest_insight"] = _insight_row(latest)
    return dashboard


def _report_body(report):
    label = "Estimated energy consumption" if report.get("is_estimated") else "Energy consumed"
    cost = report.get("estimated_cost_rand")
    cost_label = f"R{float(cost):,.2f}" if cost is not None else "unavailable (set your electricity tariff)"
    return (f"{report['date']}: {label}: {float(report['kwh']):,.1f} kWh. "
            f"Daily target: {float(report['target_kwh']):,.1f} kWh. "
            f"Difference: {float(report['difference_kwh']):+,.1f} kWh. "
            f"Estimated cost: {cost_label}. "
            f"Status: {report['status']}. Open Energy Management for analysis and recommendations.")


def _persist_reports(connection, household, dashboard):
    if dashboard.get("is_simulation"):
        return
    household_id = household["id"]
    series = dashboard.get("series", [])
    if series:
        active_alerts = {(str(alert["consumption_date"]), alert["alert_type"])
                         for alert in dashboard.get("alerts", [])}
        existing = connection.execute("""select id::text,consumption_date,alert_type,status
            from public.energy_alerts where household_id=%s and consumption_date between %s and %s
            and status in ('unread','read')""",
            (household_id, series[0]["date"], series[-1]["date"])).fetchall()
        for alert in existing:
            if (str(alert["consumption_date"]), alert["alert_type"]) not in active_alerts:
                connection.execute("""update public.energy_alerts set status='resolved',updated_at=now()
                    where id=%s and household_id=%s and status in ('unread','read')""",
                    (alert["id"], household_id))
    for alert in dashboard.get("alerts", []):
        connection.execute("""insert into public.energy_alerts(household_id,consumption_date,alert_type,severity,message)
            values(%s,%s,%s,%s,%s) on conflict(household_id,consumption_date,alert_type)
            do update set severity=excluded.severity,message=excluded.message,
              status=case when public.energy_alerts.status='resolved' then 'unread' else public.energy_alerts.status end,
              updated_at=now()""",
            (household_id, alert["consumption_date"], alert["alert_type"], alert["severity"], alert["message"]))
    for report in dashboard.get("daily_reports", []):
        if report.get("kwh") is None or report.get("is_simulation"):
            continue
        row = connection.execute("""insert into public.energy_daily_reports(household_id,consumption_date,report)
            values(%s,%s,%s::jsonb) on conflict(household_id,consumption_date) do nothing returning id::text""",
            (household_id, report["date"], _json(report))).fetchone()
        if row:
            notification = connection.execute("""insert into public.notifications(resident_id,title,body,category,official)
                values(%s,'Your Daily Energy Report',%s,'energy_daily_report',false) returning id::text""",
                (household["owner_id"], _report_body(report))).fetchone()
            connection.execute("update public.energy_daily_reports set notification_id=%s where id=%s and household_id=%s",
                               (notification["id"], row["id"], household_id))
        else:
            # A correction updates the original notification, retaining its read state.
            connection.execute("""update public.notifications n set body=%s from public.energy_daily_reports r
                where r.household_id=%s and r.consumption_date=%s and r.notification_id=n.id
                and n.resident_id=%s and r.report is distinct from %s::jsonb""",
                (_report_body(report), household_id, report["date"], household["owner_id"], _json(report)))
            connection.execute("""update public.energy_daily_reports set report=%s::jsonb,updated_at=now()
                where household_id=%s and consumption_date=%s and report is distinct from %s::jsonb""",
                (_json(report), household_id, report["date"], _json(report)))


def _session(connection, session_id, user, *, lock=False):
    # Simulation IDs are references, not authorization: require both the ID and verified owner to match.
    row = connection.execute("""select s.id::text,s.user_id::text,s.scenario,s.payload,s.created_at,s.updated_at
        from public.energy_simulation_sessions s join public.profiles p on p.id=s.user_id
        where s.id=%s and s.user_id=%s and p.role='resident' and coalesce(p.account_status,'active')='active'"""
        + (" for update of s" if lock else ""), (session_id, user["id"])).fetchone()
    if row is None:
        raise HTTPException(404, "Energy simulation not found.")
    return dict(row)


def _save_session(connection, session, user):
    connection.execute("update public.energy_simulation_sessions set payload=%s::jsonb,updated_at=now() where id=%s and user_id=%s",
                       (_json(session["payload"]), session["id"], user["id"]))


def _simulation_dashboard(session, *, days=30, start=None, end=None):
    payload = session["payload"]
    records = payload["records"]
    last = date.fromisoformat(str(records[-1]["date"]))
    start, end = _range(last, days, start, end)
    dashboard = build_dashboard(payload["household"], records, [], [], start_date=start, end_date=end,
                                as_of_date=last, is_simulation=True)
    states = payload.get("alert_states", {})
    for alert in dashboard.get("alerts", []):
        identifier = str(uuid5(NAMESPACE_URL, f"ecopulse:energy:{session['id']}:{alert['consumption_date']}:{alert['alert_type']}"))
        alert.update(id=identifier, status=states.get(identifier, "unread"))
    dashboard.update(session_id=session["id"], scenario=session["scenario"], session_created_at=session["created_at"],
                     explanations=list(payload.get("explanations", {}).values()),
                     tariff_assumption="Assumed demo tariff; simulated costs are illustrative estimates.")
    insights = payload.get("insights", {})
    current = _valid_simulation_insights(session, dashboard)
    dashboard["latest_insight"] = current[sorted(current)[-1]]["result"] if current else None
    return dashboard


def _valid_simulation_insights(session, dashboard):
    payload = session["payload"]
    result = {}
    for day, cached in payload.get("insights", {}).items():
        if not any(point["date"] == day and point.get("kwh") is not None for point in dashboard["series"]):
            continue
        explanation = payload.get("explanations", {}).get(day)
        evidence = _analysis_context(None, None, dashboard, day, session=session)
        if (cached["input_hash"] == _fingerprint(evidence, explanation)
                and datetime.fromisoformat(cached["expires_at"]) > datetime.now(timezone.utc)):
            result[day] = cached
    return result


def _explanation_row(row):
    return {"id": str(row["id"]), "date": row["consumption_date"], "reasons": row["reasons"],
            "text": row["text"], "updated_at": row.get("updated_at")}


def _insight_row(row):
    return {"id": str(row["id"]), "date": row["consumption_date"], "source": row["source"],
            "model": row["model"], "analysis": row["analysis"], "generated_at": row["generated_at"]}


def _selected_evidence(dashboard, consumption_date):
    if not any(str(row["date"]) == str(consumption_date) and row.get("kwh") is not None for row in dashboard["series"]):
        raise HTTPException(422, "There is no consumption data for this day to analyse.")
    return analysis_evidence(dashboard, consumption_date)


def _appliance_rows(connection, household):
    rows = _bounded(connection.execute("""select id::text,name as appliance_name,wattage as power_watts,
        hours_per_day as operating_hours,quantity,duty_cycle,created_at,updated_at from public.appliances
        where resident_id=%s order by created_at,id limit %s""", (household["owner_id"], MAX_ROWS + 1)).fetchall())
    return rows


def _appliance_estimates(rows, household, as_of=None):
    as_of = as_of or _today(household)
    complete = [row for row in rows if row.get("power_watts") is not None and row.get("operating_hours") is not None]
    result = appliance_analysis(complete, float(household["tariff_per_kwh"]), monthrange(as_of.year, as_of.month)[1])
    # Preserve incomplete legacy rows for correction without fabricating power/hours.
    result["appliances"].extend({**row, "daily_kwh": None, "monthly_kwh": None, "daily_cost_rand": None,
        "monthly_cost_rand": None, "percentage_of_modelled_total": None, "is_estimated": True,
        "incomplete": True} for row in rows if row not in complete)
    return result


def _analysis_context(connection, household, dashboard, day, session=None):
    evidence = _selected_evidence(dashboard, day)
    model_household = session["payload"]["household"] if session else household
    rows = session["payload"].get("appliances", []) if session else _appliance_rows(connection, household)
    estimates = _appliance_estimates(rows, model_household, date.fromisoformat(str(day)))
    evidence["appliance_estimates"] = estimates
    evidence["savings_facts"] = estimates["recommendations"]
    return evidence


def _fingerprint(evidence, explanation):
    # Row IDs and timestamps do not change the supplied explanation's meaning.
    context = {"reasons": explanation.get("reasons", []), "text": explanation.get("text", "")} if explanation else None
    return hashlib.sha256(_json({"evidence": evidence, "explanation": context,
        "model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip(), "engine": "energy-rules-v1"}).encode()).hexdigest()


def _limit_analysis(user_id):
    # This in-process quota is keyed per resident so one account cannot consume another account's allowance.
    now = clock.monotonic()
    with _analysis_attempts_lock:
        attempts = _analysis_attempts[user_id]
        while attempts and now - attempts[0] >= 60:
            attempts.popleft()
        if len(attempts) >= 8:
            raise HTTPException(429, "Please wait before requesting more energy analyses.")
        attempts.append(now)


def _analyse(connection, household, consumption_date, *, session=None, user=None):
    if session:
        payload = session["payload"]
        dashboard = _simulation_dashboard(session, days=90)
        explanation = payload.get("explanations", {}).get(str(consumption_date))
        evidence = _analysis_context(connection, household, dashboard, consumption_date, session=session)
        input_hash = _fingerprint(evidence, explanation)
        cached = payload.setdefault("insights", {}).get(str(consumption_date))
        if cached and cached["input_hash"] == input_hash and datetime.fromisoformat(cached["expires_at"]) > datetime.now(timezone.utc):
            return cached["result"]
        _limit_analysis(str(user["id"]))
        result = analyse_energy(evidence, explanation)
        result.update(id=str(uuid4()), date=str(consumption_date))
        expiry = datetime.now(timezone.utc) + timedelta(hours=24) if result["source"] == "gemini" else datetime.now(timezone.utc) + timedelta(minutes=5)
        payload["insights"][str(consumption_date)] = {"input_hash": input_hash, "result": result, "expires_at": expiry.isoformat()}
        _save_session(connection, session, user)
        return result
    _actual_date(consumption_date, household)
    dashboard = _actual_dashboard(connection, household, start=consumption_date - timedelta(days=29), end=consumption_date)
    evidence = _analysis_context(connection, household, dashboard, consumption_date)
    explanation_row = connection.execute("""select id::text,consumption_date,reasons,text,updated_at from public.energy_consumption_explanations
        where household_id=%s and consumption_date=%s""", (household["id"], consumption_date)).fetchone()
    explanation = _explanation_row(explanation_row) if explanation_row else None
    input_hash = _fingerprint(evidence, explanation)
    cached = connection.execute("""select id::text,consumption_date,source,model,analysis,generated_at from public.energy_ai_insights
        where household_id=%s and consumption_date=%s and input_hash=%s and expires_at>now()""",
        (household["id"], consumption_date, input_hash)).fetchone()
    if cached:
        return _insight_row(cached)
    _limit_analysis(str(household["owner_id"]))
    result = analyse_energy(evidence, explanation)
    expiry = datetime.now(timezone.utc) + (timedelta(hours=24) if result["source"] == "gemini" else timedelta(minutes=5))
    daily_id = next((row["id"] for row in dashboard["series"] if str(row["date"]) == str(consumption_date)), None)
    saved = connection.execute("""insert into public.energy_ai_insights(household_id,consumption_id,consumption_date,input_hash,source,model,analysis,generated_at,expires_at)
        values(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s) on conflict(household_id,consumption_date,input_hash)
        do update set source=excluded.source,model=excluded.model,analysis=excluded.analysis,generated_at=excluded.generated_at,expires_at=excluded.expires_at
        returning id::text,consumption_date,source,model,analysis,generated_at""",
        (household["id"], daily_id, consumption_date, input_hash, result["source"], result["model"], _json(result["analysis"]), result["generated_at"], expiry)).fetchone()
    return _insight_row(saved)


def _automatic_analysis(user, consumption_date):
    """Runs only after the data transaction commits, so advice cannot lose readings."""
    try:
        with _transaction() as connection:
            household = _household(connection, user, lock=True)
            return {"status": "available", "insight": _analyse(connection, household, consumption_date)}
    except Exception:
        return {"status": "unavailable", "message": "Your reading is saved. Analysis can be requested from the dashboard."}


@router.get("/settings")
@router.get("/household")
def get_household(user: dict = Depends(resident)):
    with _transaction() as connection:
        return _household(connection, user)


@router.put("/settings")
@router.put("/household")
def save_household(form: HouseholdInput, user: dict = Depends(resident)):
    with _transaction() as connection:
        household = _household(connection, user, lock=True)
        connection.execute("""update public.energy_households set name=%s,household_size=%s,daily_target_kwh=%s,
            tariff_per_kwh=%s,timezone=%s,updated_at=now() where id=%s and owner_id=%s""",
            (form.name, form.household_size, form.daily_target_kwh, form.tariff_per_kwh, form.timezone, household["id"], user["id"]))
        household = _household(connection, user)
        _actual_dashboard(connection, household, start=_today(household), end=_today(household), persist=True)
        return household


@router.get("/dashboard")
@router.get("/analytics")
def dashboard(days: int = Query(30, ge=1, le=90), start_date: date | None = None, end_date: date | None = None,
              session_id: UUID | None = None, user: dict = Depends(resident)):
    with _transaction() as connection:
        if session_id:
            return _simulation_dashboard(_session(connection, session_id, user), days=days, start=start_date, end=end_date)
        return _actual_dashboard(connection, _household(connection, user), days=days, start=start_date, end=end_date)


@router.get("/consumption")
def consumption(start_date: date | None = None, end_date: date | None = None,
                limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0, le=100000), user: dict = Depends(resident)):
    with _transaction() as connection:
        household = _household(connection, user)
        start, end = start_date or date(2000, 1, 1), end_date or _today(household)
        if start > end:
            raise HTTPException(422, "The start date must be on or before the end date.")
        params = (household["id"], start, end)
        rows = connection.execute("""select id::text,date,kwh,target_kwh,data_source,is_simulation,created_at,updated_at from public.energy_consumption_daily
            where household_id=%s and date between %s and %s order by date desc limit %s offset %s""", (*params, limit, offset)).fetchall()
        count = connection.execute("select count(*) as total from public.energy_consumption_daily where household_id=%s and date between %s and %s", params).fetchone()
        return {"items": rows, "total": count["total"], "limit": limit, "offset": offset}


@router.post("/consumption", status_code=201)
def add_consumption(form: ConsumptionInput, user: dict = Depends(resident)):
    with _transaction() as connection:
        household = _household(connection, user, lock=True)
        _actual_date(form.date, household)
        row = connection.execute("""insert into public.energy_consumption_daily(household_id,date,kwh,target_kwh)
            values(%s,%s,%s,%s) on conflict(household_id,date) do nothing
            returning id::text,date,kwh,target_kwh,data_source,is_simulation""",
            (household["id"], form.date, form.kwh, household["daily_target_kwh"])).fetchone()
        if row is None:
            raise HTTPException(409, "This day already has a manual consumption record. Edit the saved record instead.")
        _actual_dashboard(connection, household, start=form.date, end=form.date, persist=True)
    return {**dict(row), "automatic_analysis": _automatic_analysis(user, form.date)}


@router.put("/consumption/{record_id}")
def update_consumption(record_id: UUID, form: ConsumptionInput, user: dict = Depends(resident)):
    with _transaction() as connection:
        household = _household(connection, user, lock=True)
        _actual_date(form.date, household)
        existing = connection.execute("select date from public.energy_consumption_daily where id=%s and household_id=%s",
                                      (record_id, household["id"])).fetchone()
        if existing is None:
            raise HTTPException(404, "Daily energy record not found.")
        if existing["date"] != form.date:
            raise HTTPException(422, "Edit a daily record on its original date. Add a separate record for another day.")
        row = connection.execute("""update public.energy_consumption_daily set kwh=%s,updated_at=now()
            where id=%s and household_id=%s returning id::text,date,kwh,target_kwh,data_source,is_simulation""",
            (form.kwh, record_id, household["id"])).fetchone()
        _actual_dashboard(connection, household, start=form.date, end=form.date, persist=True)
    return {**dict(row), "automatic_analysis": _automatic_analysis(user, form.date)}


@router.get("/meter-readings")
def meter_readings(limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0, le=100000), user: dict = Depends(resident)):
    with _transaction() as connection:
        household = _household(connection, user)
        rows = connection.execute("""select id::text,reading_at,cumulative_kwh,is_reset,reading_source,notes,created_at from public.energy_meter_readings
            where household_id=%s order by reading_at desc limit %s offset %s""", (household["id"], limit, offset)).fetchall()
        count = connection.execute("select count(*) as total from public.energy_meter_readings where household_id=%s", (household["id"],)).fetchone()
        return {"items": rows, "total": count["total"], "limit": limit, "offset": offset}


def _validate_meter_neighbors(connection, household, form):
    if form.reading_at > datetime.now(timezone.utc):
        raise HTTPException(422, "Actual meter readings cannot be dated in the future.")
    _actual_date(form.reading_at.astimezone(ZoneInfo(household["timezone"])).date(), household)
    previous = connection.execute("""select cumulative_kwh from public.energy_meter_readings
        where household_id=%s and reading_at<%s order by reading_at desc limit 1""", (household["id"], form.reading_at)).fetchone()
    following = connection.execute("""select cumulative_kwh,is_reset from public.energy_meter_readings
        where household_id=%s and reading_at>%s order by reading_at limit 1""", (household["id"], form.reading_at)).fetchone()
    if previous and not form.is_reset and form.cumulative_kwh < previous["cumulative_kwh"]:
        raise HTTPException(422, "The meter value decreased. Mark a meter reset to establish a new baseline.")
    if following and not following["is_reset"] and form.cumulative_kwh > following["cumulative_kwh"]:
        raise HTTPException(422, "This value is above the next saved meter reading. Check the chronology or mark the next reading as a reset.")


@router.post("/meter-readings", status_code=201)
def add_meter_reading(form: MeterInput, user: dict = Depends(resident)):
    with _transaction() as connection:
        household = _household(connection, user, lock=True)
        _validate_meter_neighbors(connection, household, form)
        previous = connection.execute("""select reading_at from public.energy_meter_readings
            where household_id=%s and reading_at<%s order by reading_at desc limit 1""",
            (household["id"], form.reading_at)).fetchone()
        following = connection.execute("""select reading_at from public.energy_meter_readings
            where household_id=%s and reading_at>%s order by reading_at limit 1""",
            (household["id"], form.reading_at)).fetchone()
        row = connection.execute("""insert into public.energy_meter_readings(household_id,reading_at,cumulative_kwh,is_reset,notes)
            values(%s,%s,%s,%s,%s) returning id::text,reading_at,cumulative_kwh,is_reset,reading_source,notes""",
            (household["id"], form.reading_at, form.cumulative_kwh, form.is_reset, form.notes)).fetchone()
        zone = ZoneInfo(household["timezone"])
        day = form.reading_at.astimezone(zone).date()
        affected_start = previous["reading_at"].astimezone(zone).date() if previous else day
        affected_end = following["reading_at"].astimezone(zone).date() if following else day
        today = _today(household)
        affected_start = max(affected_start, today - timedelta(days=89))
        affected_end = min(affected_end, today)
        analysis_date = None
        if affected_start <= affected_end:
            dashboard = _actual_dashboard(connection, household, start=affected_start, end=affected_end, persist=True)
            available = [point["date"] for point in dashboard["series"] if point.get("kwh") is not None]
            analysis_date = date.fromisoformat(str(available[-1])) if available else None
    result = dict(row)
    if analysis_date:
        result["automatic_analysis"] = _automatic_analysis(user, analysis_date)
    return result


@router.get("/alerts")
def alerts(session_id: UUID | None = None, limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0, le=100000),
           user: dict = Depends(resident)):
    with _transaction() as connection:
        if session_id:
            items = _simulation_dashboard(_session(connection, session_id, user), days=90)["alerts"]
            return {"items": items[offset:offset + limit], "total": len(items), "limit": limit, "offset": offset}
        household = _household(connection, user)
        _actual_dashboard(connection, household)
        items = connection.execute("""select id::text,consumption_date,alert_type,severity,message,status,created_at from public.energy_alerts
            where household_id=%s order by consumption_date desc,created_at desc limit %s offset %s""", (household["id"], limit, offset)).fetchall()
        count = connection.execute("select count(*) as total from public.energy_alerts where household_id=%s", (household["id"],)).fetchone()
        return {"items": items, "total": count["total"], "limit": limit, "offset": offset}


@router.patch("/alerts/{alert_id}")
def update_alert(alert_id: UUID, form: AlertInput, session_id: UUID | None = None, user: dict = Depends(resident)):
    with _transaction() as connection:
        if session_id:
            session = _session(connection, session_id, user, lock=True)
            if not any(row["id"] == str(alert_id) for row in _simulation_dashboard(session, days=90)["alerts"]):
                raise HTTPException(404, "Energy alert not found in this simulation.")
            session["payload"].setdefault("alert_states", {})[str(alert_id)] = form.status
            _save_session(connection, session, user)
            return {"id": str(alert_id), "status": form.status}
        household = _household(connection, user, lock=True)
        row = connection.execute("""update public.energy_alerts set status=%s,updated_at=now() where id=%s and household_id=%s
            returning id::text,status""", (form.status, alert_id, household["id"])).fetchone()
        if row is None:
            raise HTTPException(404, "Energy alert not found.")
        return row


@router.post("/explanations")
def save_explanation(form: ExplanationInput, user: dict = Depends(resident)):
    with _transaction() as connection:
        if form.session_id:
            session = _session(connection, form.session_id, user, lock=True)
            _selected_evidence(_simulation_dashboard(session, days=90), form.date)
            existing = session["payload"].setdefault("explanations", {}).get(str(form.date), {})
            explanation = {"id": existing.get("id", str(uuid4())), "date": str(form.date), "reasons": form.reasons,
                           "text": form.text.strip(), "updated_at": datetime.now(timezone.utc).isoformat()}
            session["payload"]["explanations"][str(form.date)] = explanation
            _save_session(connection, session, user)
            insight = _analyse(connection, None, form.date, session=session, user=user)
            return {"explanation": explanation, "insight": insight}
        household = _household(connection, user, lock=True)
        _actual_date(form.date, household)
        dashboard = _actual_dashboard(connection, household, start=form.date, end=form.date)
        _selected_evidence(dashboard, form.date)
        daily_id = next((row["id"] for row in dashboard["series"] if str(row["date"]) == str(form.date)), None)
        row = connection.execute("""insert into public.energy_consumption_explanations(household_id,consumption_id,consumption_date,reasons,text)
            values(%s,%s,%s,%s,%s) on conflict(household_id,consumption_date) do update
            set consumption_id=excluded.consumption_id,reasons=excluded.reasons,text=excluded.text,updated_at=now()
            returning id::text,consumption_date,reasons,text,updated_at""",
            (household["id"], daily_id, form.date, form.reasons, form.text.strip())).fetchone()
        return {"explanation": _explanation_row(row), "insight": _analyse(connection, household, form.date)}


@router.post("/ai/analyse")
def analyse(form: AnalysisInput, user: dict = Depends(resident)):
    with _transaction() as connection:
        if form.session_id:
            return _analyse(connection, None, form.date, session=_session(connection, form.session_id, user, lock=True), user=user)
        return _analyse(connection, _household(connection, user, lock=True), form.date)


@router.get("/insights")
def insights(consumption_date: date | None = Query(default=None, alias="date"), session_id: UUID | None = None,
             limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0, le=100000),
             selected_date: date | None = Query(default=None, alias="consumption_date"), user: dict = Depends(resident)):
    if selected_date is not None:
        if consumption_date is not None and selected_date != consumption_date:
            raise HTTPException(422, "Choose a single consumption date.")
        consumption_date = selected_date
    with _transaction() as connection:
        if session_id:
            session = _session(connection, session_id, user)
            payload = session["payload"]
            current = _valid_simulation_insights(session, _simulation_dashboard(session, days=90))
            keys = [str(consumption_date)] if consumption_date else sorted(current, reverse=True)
            items = [current[key]["result"] for key in keys if key in current]
            return {"items": items[offset:offset + limit], "explanation": payload.get("explanations", {}).get(str(consumption_date)) if consumption_date else None}
        household = _household(connection, user)
        if consumption_date is not None:
            _actual_date(consumption_date, household)
            dashboard = _actual_dashboard(connection, household,
                                          start=consumption_date - timedelta(days=29),
                                          end=consumption_date)
            explanation = connection.execute("""select id::text,consumption_date,reasons,text,updated_at
                from public.energy_consumption_explanations where household_id=%s and consumption_date=%s""",
                (household["id"], consumption_date)).fetchone()
            current_hash = None
            if any(str(row["date"]) == str(consumption_date) and row.get("kwh") is not None
                   for row in dashboard["series"]):
                current_hash = _fingerprint(_analysis_context(connection, household, dashboard, consumption_date),
                                            _explanation_row(explanation) if explanation else None)
            items = connection.execute("""select id::text,consumption_date,source,model,analysis,generated_at
                from public.energy_ai_insights where household_id=%s and consumption_date=%s
                  and input_hash=%s and expires_at>now()
                order by generated_at desc limit %s offset %s""",
                (household["id"], consumption_date, current_hash or "0" * 64, limit, offset)).fetchall()
            return {"items": [_insight_row(row) for row in items],
                    "explanation": _explanation_row(explanation) if explanation else None}
        params = (household["id"], consumption_date, consumption_date, limit, offset)
        items = connection.execute("""select id::text,consumption_date,source,model,analysis,generated_at from public.energy_ai_insights
            where household_id=%s and (%s::date is null or consumption_date=%s) order by consumption_date desc,generated_at desc limit %s offset %s""", params).fetchall()
        explanation = connection.execute("""select id::text,consumption_date,reasons,text,updated_at from public.energy_consumption_explanations
            where household_id=%s and consumption_date=%s""", (household["id"], consumption_date)).fetchone() if consumption_date else None
        return {"items": [_insight_row(row) for row in items], "explanation": _explanation_row(explanation) if explanation else None}


@router.get("/simulation")
def latest_simulation(user: dict = Depends(resident)):
    with _transaction() as connection:
        # An account may have been suspended after its JWT was issued.
        row = connection.execute("""select s.id::text,s.user_id::text,s.scenario,s.payload,s.created_at,s.updated_at
            from public.energy_simulation_sessions s join public.profiles p on p.id=s.user_id
            where s.user_id=%s and p.role='resident' and coalesce(p.account_status,'active')='active'
            order by s.created_at desc limit 1""", (user["id"],)).fetchone()
        return _simulation_dashboard(dict(row)) if row else None


@router.post("/simulation/generate", status_code=201)
def generate_simulation(form: SimulationInput, user: dict = Depends(resident)):
    with _transaction() as connection:
        household = _household(connection, user, provision=False)
        session_id = str(uuid4())
        last = _today(household)
        records = generate_simulation_records(form.scenario, form.days, form.daily_target_kwh,
                                               form.household_size, last - timedelta(days=form.days - 1), seed=session_id)
        simulation_household = {**household, "household_size": form.household_size, "daily_target_kwh": form.daily_target_kwh,
                                "tariff_per_kwh": form.tariff_per_kwh, "name": "Simulated household"}
        payload = {"household": simulation_household, "records": records, "explanations": {}, "insights": {},
                   "alert_states": {}, "appliances": [], "purchases": [], "balances": []}
        row = connection.execute("""insert into public.energy_simulation_sessions(id,user_id,scenario,payload)
            values(%s,%s,%s,%s::jsonb) returning id::text,user_id::text,scenario,payload,created_at,updated_at""",
            (session_id, user["id"], form.scenario, _json(payload))).fetchone()
        return _simulation_dashboard(dict(row), days=form.days)


def _advance_simulation(connection, session, user, *, spike=False, geyser=False):
    payload = session["payload"]
    records = payload["records"]
    if len(records) >= MAX_SIMULATION_DAYS:
        raise HTTPException(422, "This simulation has reached 90 days. Reset it to start again.")
    household = payload["household"]
    first = date.fromisoformat(str(records[0]["date"]))
    next_date = date.fromisoformat(str(records[-1]["date"])) + timedelta(days=1)
    generated = generate_simulation_records(session["scenario"], len(records) + 1, float(household["daily_target_kwh"]),
                                            household["household_size"], first, seed=session["id"])
    next_record = generated[-1]
    next_record["date"] = next_date.isoformat()
    if spike or geyser:
        next_record = simulation_spike_record(records, float(household["daily_target_kwh"]),
                                             geyser=geyser, seed=session["id"])
    records.append(next_record)
    _save_session(connection, session, user)
    return _simulation_dashboard(session, days=min(90, len(records)))


@router.post("/simulation/next-day")
def next_day(form: SessionInput, user: dict = Depends(resident)):
    with _transaction() as connection:
        return _advance_simulation(connection, _session(connection, form.session_id, user, lock=True), user)


@router.post("/simulation/trigger-spike")
def trigger_spike(form: SessionInput, user: dict = Depends(resident)):
    with _transaction() as connection:
        return _advance_simulation(connection, _session(connection, form.session_id, user, lock=True), user, spike=True)


@router.post("/simulation/geyser-overuse")
@router.post("/simulation/trigger-geyser")
def trigger_geyser(form: SessionInput, user: dict = Depends(resident)):
    with _transaction() as connection:
        return _advance_simulation(connection, _session(connection, form.session_id, user, lock=True), user, geyser=True)


@router.post("/simulation/reset")
def reset_simulation(form: SessionInput, user: dict = Depends(resident)):
    with _transaction() as connection:
        session = _session(connection, form.session_id, user, lock=True)
        connection.execute("delete from public.energy_simulation_sessions where id=%s and user_id=%s", (session["id"], user["id"]))
        return {"reset": True, "session_id": session["id"]}


@router.get("/appliances")
def appliances(session_id: UUID | None = None, user: dict = Depends(resident)):
    with _transaction() as connection:
        if session_id:
            session = _session(connection, session_id, user)
            return _appliance_estimates(session["payload"].get("appliances", []), session["payload"]["household"],
                                       date.fromisoformat(session["payload"]["records"][-1]["date"]))
        household = _household(connection, user)
        return _appliance_estimates(_appliance_rows(connection, household), household)


def _save_appliance(connection, user, form, record_id=None, session_id=None):
    if session_id:
        session = _session(connection, session_id, user, lock=True)
        items = session["payload"].setdefault("appliances", [])
        if record_id:
            existing = next((row for row in items if row["id"] == str(record_id)), None)
            if existing is None:
                raise HTTPException(404, "Simulated appliance not found.")
            existing.update(form.model_dump(), updated_at=datetime.now(timezone.utc).isoformat())
            result = existing
        else:
            if len(items) >= 100:
                raise HTTPException(422, "Keep simulation appliance models to at most 100 entries.")
            result = {"id": str(uuid4()), **form.model_dump(), "is_simulation": True}
            items.append(result)
        _save_session(connection, session, user)
        return result
    household = _household(connection, user, lock=True)
    values = (form.appliance_name, form.power_watts, form.operating_hours, form.quantity, form.duty_cycle)
    if record_id:
        row = connection.execute("""update public.appliances set name=%s,wattage=%s,hours_per_day=%s,
            quantity=%s,duty_cycle=%s,updated_at=now() where id=%s and resident_id=%s
            returning id::text,name as appliance_name,wattage as power_watts,hours_per_day as operating_hours,
            quantity,duty_cycle,created_at,updated_at""", (*values, record_id, household["owner_id"])).fetchone()
        if row is None:
            raise HTTPException(404, "Appliance not found.")
    else:
        count = connection.execute("select count(*) as total from public.appliances where resident_id=%s", (household["owner_id"],)).fetchone()
        if count["total"] >= 100:
            raise HTTPException(422, "Keep appliance models to at most 100 entries.")
        row = connection.execute("""insert into public.appliances(name,wattage,hours_per_day,quantity,duty_cycle,resident_id)
            values(%s,%s,%s,%s,%s,%s) returning id::text,name as appliance_name,wattage as power_watts,
            hours_per_day as operating_hours,quantity,duty_cycle,created_at,updated_at""", (*values, household["owner_id"])).fetchone()
    return dict(row)


@router.post("/appliances", status_code=201)
def add_appliance(form: ApplianceInput, session_id: UUID | None = None, user: dict = Depends(resident)):
    with _transaction() as connection:
        return _save_appliance(connection, user, form, session_id=session_id)


@router.put("/appliances/{record_id}")
def update_appliance(record_id: UUID, form: ApplianceInput, session_id: UUID | None = None, user: dict = Depends(resident)):
    with _transaction() as connection:
        return _save_appliance(connection, user, form, record_id=record_id, session_id=session_id)


@router.delete("/appliances/{record_id}")
def delete_appliance(record_id: UUID, session_id: UUID | None = None, user: dict = Depends(resident)):
    with _transaction() as connection:
        if session_id:
            session = _session(connection, session_id, user, lock=True)
            rows = session["payload"].setdefault("appliances", [])
            if not any(row["id"] == str(record_id) for row in rows):
                raise HTTPException(404, "Simulated appliance not found.")
            session["payload"]["appliances"] = [row for row in rows if row["id"] != str(record_id)]
            _save_session(connection, session, user)
        else:
            household = _household(connection, user, lock=True)
            row = connection.execute("delete from public.appliances where id=%s and resident_id=%s returning id::text",
                                     (record_id, household["owner_id"])).fetchone()
            if row is None:
                raise HTTPException(404, "Appliance not found.")
        return {"deleted": True, "id": str(record_id)}


@router.get("/prepaid")
def prepaid(session_id: UUID | None = None, user: dict = Depends(resident)):
    with _transaction() as connection:
        if session_id:
            session = _session(connection, session_id, user)
            dashboard = _simulation_dashboard(session, days=90)
            purchases, balances = session["payload"].get("purchases", []), session["payload"].get("balances", [])
        else:
            household = _household(connection, user)
            dashboard = _actual_dashboard(connection, household, days=90)
            purchases = _bounded(connection.execute("""select id::text,purchase_date,amount_zar,units_kwh,created_at
                from public.energy_prepaid_purchases where household_id=%s order by purchase_date,id limit %s""",
                (household["id"], MAX_ROWS + 1)).fetchall())
            balances = _bounded(connection.execute("""select id::text,observed_at,remaining_kwh,created_at
                from public.energy_prepaid_balances where household_id=%s order by observed_at,id limit %s""",
                (household["id"], MAX_ROWS + 1)).fetchall())
        return {"purchases": purchases, "balances": balances,
                "summary": prepaid_summary(purchases, balances, dashboard, as_of_date=date.fromisoformat(str(dashboard["as_of_date"])))}


def _prepaid_date(stamp, household):
    if stamp > datetime.now(timezone.utc):
        raise HTTPException(422, "Actual prepaid records cannot be dated in the future.")
    _actual_date(stamp.astimezone(ZoneInfo(household["timezone"])).date(), household)


@router.post("/prepaid/purchases", status_code=201)
def add_prepaid_purchase(form: PrepaidPurchaseInput, user: dict = Depends(resident)):
    with _transaction() as connection:
        household = _household(connection, user, lock=True)
        _prepaid_date(form.purchase_date, household)
        return connection.execute("""insert into public.energy_prepaid_purchases(household_id,purchase_date,amount_zar,units_kwh)
            values(%s,%s,%s,%s) returning id::text,purchase_date,amount_zar,units_kwh,created_at""",
            (household["id"], form.purchase_date, form.amount_zar, form.units_kwh)).fetchone()


@router.post("/prepaid/balances", status_code=201)
def add_prepaid_balance(form: PrepaidBalanceInput, user: dict = Depends(resident)):
    with _transaction() as connection:
        household = _household(connection, user, lock=True)
        _prepaid_date(form.observed_at, household)
        return connection.execute("""insert into public.energy_prepaid_balances(household_id,observed_at,remaining_kwh)
            values(%s,%s,%s) returning id::text,observed_at,remaining_kwh,created_at""",
            (household["id"], form.observed_at, form.remaining_kwh)).fetchone()


@router.get("/advisor")
def saving_advisor(session_id: UUID | None = None, user: dict = Depends(resident)):
    with _transaction() as connection:
        if session_id:
            session = _session(connection, session_id, user)
            household = session["payload"]["household"]
            dashboard = _simulation_dashboard(session)
            rows = session["payload"].get("appliances", [])
        else:
            household = _household(connection, user)
            dashboard = _actual_dashboard(connection, household)
            rows = _appliance_rows(connection, household)
        estimates = _appliance_estimates(rows, household, date.fromisoformat(str(dashboard["as_of_date"])))
        return {"recommendations": estimates["recommendations"], "appliance_estimates": estimates,
                "insight": dashboard.get("latest_insight"), "disclaimer": estimates["disclaimer"]}

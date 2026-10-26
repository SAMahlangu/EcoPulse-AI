"""Verify energy SQL/API/isolation in a rollback-only transaction.

--verify never commits fixtures or schema. --apply-schema commits only migration 015.
Provider requests are disabled for verification; no email or incident is sent.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, time, timedelta
import json
import logging
import os
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MIGRATION = ROOT / "database/migrations/015_energy_consumption_intelligence.sql"
TABLES = (
    "energy_households", "energy_consumption_daily", "energy_meter_readings",
    "energy_consumption_explanations", "energy_ai_insights", "energy_alerts",
    "energy_daily_reports", "energy_simulation_sessions", "energy_prepaid_purchases",
    "energy_prepaid_balances",
)


class VerificationFailure(AssertionError):
    """A fixed diagnostic which contains no database credentials or user text."""


def check(condition, message):
    if not condition:
        raise VerificationFailure(message)


def apply_schema(connection):
    existing = connection.execute(
        "select table_name from information_schema.tables where table_schema='public' and table_name=any(%s)",
        (list(TABLES),),
    ).fetchall()
    if len(existing) == len(TABLES):
        return
    check(not existing, "Partial energy schema exists; review it before applying migration 015.")
    sql = "\n".join(line for line in MIGRATION.read_text(encoding="utf-8").splitlines()
                    if line.strip().lower() not in {"begin;", "commit;"})
    connection.execute(sql)
    count = connection.execute(
        "select count(*) n from information_schema.tables where table_schema='public' and table_name=any(%s)",
        (list(TABLES),),
    ).fetchone()["n"]
    check(count == len(TABLES), "Migration did not create all energy tables.")


def fixture_owner(connection):
    actor = str(uuid4())
    connection.execute("insert into auth.users(id,email) values(%s,%s)",
                       (actor, f"energy-rollback-{actor}@example.invalid"))
    connection.execute("update public.profiles set role='resident',account_status='active' where id=%s", (actor,))
    check(connection.execute("select 1 from public.profiles where id=%s", (actor,)).fetchone(),
          "Synthetic auth owner has no profile.")
    return {"id": actor, "role": "resident", "municipality_id": None, "municipality_ids": []}


def predicate(table):
    if table == "energy_households":
        return "owner_id=%s"
    if table == "energy_simulation_sessions":
        return "user_id=%s"
    return "household_id in (select id from public.energy_households where owner_id=%s)"


def assert_denied(connection, statement, params=()):
    import psycopg
    try:
        with connection.transaction():
            connection.execute(statement, params)
    except psycopg.errors.InsufficientPrivilege:
        return
    raise VerificationFailure("A direct database operation bypassed the energy API boundary.")


def verify_rls(connection, owner, other):
    for actor, owns in ((owner, True), (other, False)):
        connection.execute("set local role authenticated")
        connection.execute("select set_config('request.jwt.claims',%s,true)",
                           (json.dumps({"sub": actor["id"], "role": "authenticated"}),))
        for table in TABLES:
            count = connection.execute(f"select count(*) n from public.{table} where {predicate(table)}",
                                       (owner["id"],)).fetchone()["n"]
            check(count > 0 if owns else count == 0, f"Owner isolation failed for {table}.")
            assert_denied(connection, f"delete from public.{table}")
            assert_denied(connection, f"update public.{table} set id=id")
        connection.execute("reset role")
    connection.execute("update public.profiles set account_status='suspended' where id=%s", (owner["id"],))
    connection.execute("set local role authenticated")
    connection.execute("select set_config('request.jwt.claims',%s,true)",
                       (json.dumps({"sub": owner["id"], "role": "authenticated"}),))
    for table in TABLES:
        check(connection.execute(f"select count(*) n from public.{table}").fetchone()["n"] == 0,
              f"Suspended resident retained access to {table}.")
    connection.execute("reset role")
    connection.execute("update public.profiles set account_status='active' where id=%s", (owner["id"],))
    connection.execute("set local role anon")
    for table in TABLES:
        assert_denied(connection, f"select * from public.{table}")
    connection.execute("reset role")
    print("PASS: PostgreSQL RLS for owner, other resident, suspended resident and anonymous users; API-only writes.")


def verify_api(connection, client, owner, other, app, identity):
    def call(method, path, body=None, status=200):
        with connection.transaction(force_rollback=status >= 400):
            response = client.request(method, "/api/v1/energy" + path, json=body)
            check(response.status_code == status,
                  f"{method} /energy{path.split('?')[0]} returned {response.status_code}; expected {status}.")
            return response.json() if response.content else None

    def snapshot():
        result = {table: connection.execute(
            f"select to_jsonb(t) row from public.{table} t where {predicate(table)} order by id", (owner["id"],)
        ).fetchall() for table in TABLES if table != "energy_simulation_sessions"}
        result["notifications"] = connection.execute(
            "select to_jsonb(n) row from public.notifications n where resident_id=%s order by id", (owner["id"],)
        ).fetchall()
        result["appliances"] = connection.execute(
            "select to_jsonb(a) row from public.appliances a where resident_id=%s order by id", (owner["id"],)
        ).fetchall()
        return result

    settings = {"name": "Rollback energy household", "household_size": 4, "daily_target_kwh": 15,
                "tariff_per_kwh": 2.75, "timezone": "Africa/Johannesburg"}
    household = call("PUT", "/household", settings)
    today = datetime.now(ZoneInfo(household["timezone"])).date()
    empty = call("GET", "/dashboard")
    check(empty["summary"]["today_kwh"] is None and not empty["daily_reports"],
          "Missing data became recorded use or generated a report.")
    call("POST", "/consumption", {"date": str(today), "kwh": -1}, status=422)
    for offset in range(6, 0, -1):
        call("POST", "/consumption", {"date": str(today - timedelta(days=offset)), "kwh": 14}, status=201)
    daily = call("POST", "/consumption", {"date": str(today), "kwh": 22}, status=201)
    summary = call("GET", "/dashboard")["summary"]
    check(summary["today_kwh"] == 22 and summary["difference_kwh"] == 7 and
          abs(summary["percentage_vs_target"] - 46.67) < .01, "22 / 15 kWh target math is wrong.")
    check(summary["difference_vs_yesterday_kwh"] == 8, "Previous-day comparison is wrong.")
    call("POST", "/consumption", {"date": str(today), "kwh": 22}, status=409)
    call("PUT", "/consumption/" + daily["id"], {"date": str(today), "kwh": 25})
    alerts = call("GET", "/alerts")["items"]
    check(alerts, "Target exceedance did not produce an alert.")
    before = snapshot()
    call("GET", "/dashboard")
    call("GET", "/dashboard")
    check(snapshot() == before, "Read requests unexpectedly modified actual records.")
    call("PATCH", "/alerts/" + alerts[0]["id"], {"status": "dismissed"})
    analysed = call("POST", "/ai/analyse", {"date": str(today)})
    cached = call("POST", "/ai/analyse", {"date": str(today)})
    check(analysed["source"] == "rules" and cached["id"] == analysed["id"], "Fallback or AI cache failed.")
    explanation = {"date": str(today), "reasons": ["geyser_usage", "visitors"], "text": "Visitors used more hot water."}
    explained = call("POST", "/explanations", explanation)
    explanation["text"] = "Visitors left; we are monitoring usage."
    updated = call("POST", "/explanations", explanation)
    check(explained["explanation"]["id"] == updated["explanation"]["id"] and
          explained["insight"]["id"] != updated["insight"]["id"], "Explanation update/cache invalidation failed.")
    print("PASS: real SQL daily CRUD, target/cost math, alerts, report deduplication, cached AI fallback and explanations.")

    meter_day = today - timedelta(days=20)
    for offset, value, reset in ((0, 1250, False), (2, 1286, False), (3, 0, True), (4, 18, False)):
        stamp = datetime.combine(meter_day + timedelta(days=offset), time.min, ZoneInfo(household["timezone"]))
        call("POST", "/meter-readings", {"reading_at": stamp.isoformat(), "cumulative_kwh": value,
                                         "is_reset": reset}, status=201)
    measured = call("GET", f"/dashboard?start_date={meter_day}&end_date={meter_day + timedelta(days=4)}")
    points = {point["date"]: point for point in measured["series"]}
    check(points[str(meter_day)]["kwh"] == 18 and points[str(meter_day)]["is_estimated"],
          "Meter interval allocation is wrong or lacks its estimate label.")
    check(points[str(meter_day + timedelta(days=2))]["kwh"] is None, "Reset invented an unmeasured interval.")
    check(points[str(meter_day + timedelta(days=3))]["kwh"] == 18, "Post-reset meter difference is wrong.")
    print("PASS: cumulative meter differences, multi-day estimates and reset baseline.")

    appliance = call("POST", "/appliances", {"appliance_name": "Electric heater", "power_watts": 2000,
        "operating_hours": 5, "quantity": 1, "duty_cycle": 1}, status=201)
    models = call("GET", "/appliances")
    model = next(row for row in models["appliances"] if row["id"] == appliance["id"])
    check(model["daily_kwh"] == 10 and model["daily_cost_rand"] == 27.5,
          "Appliance energy/cost calculation is wrong.")
    call("PUT", "/appliances/" + appliance["id"], {"appliance_name": "Electric heater", "power_watts": 2000,
        "operating_hours": 5, "quantity": 1, "duty_cycle": .5})
    check(call("GET", "/appliances")["total_daily_kwh"] == 5, "Appliance duty cycle update failed.")
    balance_at = datetime.combine(today - timedelta(days=2), time.min, ZoneInfo(household["timezone"]))
    call("POST", "/prepaid/purchases", {"purchase_date": (balance_at + timedelta(days=1, hours=12)).isoformat(),
        "amount_zar": 137.5, "units_kwh": 50}, status=201)
    check(call("GET", "/prepaid")["summary"]["estimated_remaining_kwh"] is None,
          "A purchase alone invented a remaining balance.")
    call("POST", "/prepaid/balances", {"observed_at": balance_at.isoformat(), "remaining_kwh": 100}, status=201)
    check(call("GET", "/prepaid")["summary"]["estimated_remaining_kwh"] == 97,
          "Prepaid balance did not reconcile known units, purchases and daily consumption.")
    print("PASS: reused appliance inventory, duty cycle, modelled costs and known-balance prepaid reconciliation.")

    before = snapshot()
    simulation = call("POST", "/simulation/generate", {"days": 30, "daily_target_kwh": 15, "scenario": "normal"}, status=201)
    sid = simulation["session_id"]
    check(len(simulation["series"]) == 30 and simulation["is_simulation"], "Simulation did not generate thirty labeled days.")
    call("POST", "/simulation/next-day", {"session_id": sid})
    call("POST", "/simulation/trigger-spike", {"session_id": sid})
    geyser = call("POST", "/simulation/geyser-overuse", {"session_id": sid})
    check(geyser["summary"]["today_kwh"] > 22.5 and geyser["alerts"], "Geyser simulation did not generate a critical increase.")
    sim_date = geyser["as_of_date"]
    sim_analysis = call("POST", "/ai/analyse", {"date": sim_date, "session_id": sid})
    check("simulat" in sim_analysis["analysis"]["caveat"].lower(), "Simulation analysis lacks its synthetic-data caveat.")
    call("POST", "/explanations", {**explanation, "date": sim_date, "session_id": sid})
    check(snapshot() == before, "Simulation changed actual records or customer notifications.")
    app.dependency_overrides[identity] = lambda: other
    call("GET", "/dashboard?session_id=" + sid, status=404)
    call("POST", "/simulation/reset", {"session_id": sid}, status=404)
    call("PUT", "/consumption/" + daily["id"], {"date": str(today), "kwh": 1}, status=404)
    call("DELETE", "/appliances/" + appliance["id"], status=404)
    check(not call("GET", "/appliances")["appliances"] and not call("GET", "/prepaid")["purchases"],
          "Another household could list appliance or prepaid records.")
    app.dependency_overrides[identity] = lambda: owner
    call("POST", "/simulation/reset", {"session_id": sid})
    check(snapshot() == before and call("GET", "/simulation") is None, "Reset changed actual records or retained its session.")
    call("POST", "/simulation/generate", {"days": 7}, status=201)
    print("PASS: simulation generation, next day, spike, geyser, explanations, reset and household isolation.")

def verify_database(connection):
    from fastapi.testclient import TestClient
    from backend.app import energy, main
    from backend.app.security import current_user
    owner, other = fixture_owner(connection), fixture_owner(connection)

    @contextmanager
    def shared_connection():
        yield connection

    previous = dict(main.app.dependency_overrides)
    try:
        # Energy uses PostgreSQL only. Request without the application's unrelated
        # SQLite lifespan so Windows cannot retain handles to a temporary file.
        with patch.object(energy, "postgres_connection", shared_connection), \
             patch.object(energy, "_limit_analysis", lambda _actor: None), \
             patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            client = TestClient(main.app)
            try:
                check(client.get("/api/v1/energy/dashboard").status_code == 401, "Energy dashboard is unauthenticated.")
                main.app.dependency_overrides[current_user] = lambda: owner
                verify_api(connection, client, owner, other, main.app, current_user)
            finally:
                client.close()
        verify_rls(connection, owner, other)
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    operations = parser.add_mutually_exclusive_group(required=True)
    operations.add_argument("--verify", action="store_true")
    operations.add_argument("--apply-schema", action="store_true")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / "backend/.env")
    from backend.app.security import postgres_connection
    sys.stdout.reconfigure(line_buffering=True)
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with postgres_connection() as connection:
            with connection.transaction(force_rollback=args.verify):
                connection.execute("set local statement_timeout='30s'")
                connection.execute("set local lock_timeout='5s'")
                apply_schema(connection)
                if args.verify:
                    apply_schema(connection)
                    verify_database(connection)
        print("PASS: all energy fixtures and schema changes rolled back." if args.verify else
              "Energy migration 015 committed; no demonstration records created.")
        return 0
    except Exception as exc:
        print(f"Energy {'verification' if args.verify else 'migration'} failed ({type(exc).__name__}). No partial transaction committed.")
        if isinstance(exc, VerificationFailure):
            print(str(exc))
        return 1
    finally:
        logging.disable(previous_logging)


if __name__ == "__main__":
    raise SystemExit(main())

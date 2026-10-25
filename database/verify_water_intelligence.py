"""Validate Smart Water SQL/API/RLS in one rollback-only transaction.

Run from the repository root with backend/venv/Scripts/python.exe.
--verify never commits its migration, synthetic accounts, readings or notifications.
--apply-schema explicitly commits only the additive migration, with no demo data.
The two operations cannot be combined. No provider, email or incident is contacted.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
import json
import logging
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MIGRATION = ROOT / "database/migrations/014_water_consumption_intelligence.sql"
TABLES = (
    "water_households", "water_consumption_daily", "water_meter_readings",
    "water_consumption_explanations", "water_ai_insights", "water_alerts",
    "water_simulation_sessions", "water_daily_reports",
)


class VerificationFailure(AssertionError):
    """Safe, fixed diagnostic text, without database driver or auth payloads."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationFailure(message)


def apply_schema(connection) -> None:
    existing = connection.execute(
        "select table_name from information_schema.tables "
        "where table_schema='public' and table_name=any(%s)", (list(TABLES),)
    ).fetchall()
    if len(existing) == len(TABLES):
        # Keep previously applied migrations immutable. Replay means preserve, not rebuild.
        return
    check(not existing, "Partial Smart Water schema exists; inspect it before applying migration 014.")
    # This script owns the transaction, including the unconditional --verify rollback.
    sql = "\n".join(
        line for line in MIGRATION.read_text(encoding="utf-8").splitlines()
        if line.strip().lower() not in {"begin;", "commit;"}
    )
    connection.execute(sql)
    rows = connection.execute(
        "select table_name from information_schema.tables "
        "where table_schema='public' and table_name=any(%s)", (list(TABLES),)
    ).fetchall()
    check(len(rows) == len(TABLES), "Migration did not create all Smart Water tables.")


def fixture_owner(connection) -> dict:
    actor = str(uuid4())
    connection.execute(
        "insert into auth.users(id,email) values(%s,%s)",
        (actor, f"water-rollback-{actor}@example.invalid"),
    )
    connection.execute(
        "update public.profiles set role='resident',account_status='active' where id=%s", (actor,)
    )
    check(connection.execute("select 1 from public.profiles where id=%s", (actor,)).fetchone() is not None,
          "Synthetic auth owner did not receive a resident profile.")
    return {"id": actor, "role": "resident", "municipality_id": None, "municipality_ids": []}


def request(client, method: str, path: str, payload=None, *, status: int = 200):
    response = client.request(method, "/api/v1/water" + path, json=payload)
    check(response.status_code == status, f"{method} /water{path.split('?')[0]} returned HTTP {response.status_code}; expected {status}.")
    return response.json() if response.content else None


def assert_denied(connection, sql: str, params=()) -> None:
    import psycopg
    try:
        with connection.transaction():
            connection.execute(sql, params)
    except psycopg.errors.InsufficientPrivilege:
        return
    raise VerificationFailure("A direct database operation bypassed the water API write boundary.")


def verify_rls(connection, owner: dict, other: dict) -> None:
    """Exercise actual PostgreSQL roles; no mock emulates row-level security."""
    for actor, own in ((owner, True), (other, False)):
        connection.execute("set local role authenticated")
        connection.execute(
            "select set_config('request.jwt.claims',%s,true)",
            (json.dumps({"sub": actor["id"], "role": "authenticated"}),),
        )
        for table in TABLES:
            predicate = "owner_id=%s" if table == "water_households" else (
                "user_id=%s" if table == "water_simulation_sessions" else
                "household_id in (select id from public.water_households where owner_id=%s)"
            )
            count = connection.execute(f"select count(*) n from public.{table} where {predicate}", (owner["id"],)).fetchone()["n"]
            check(count > 0 if own else count == 0,
                  f"Authenticated {'owner' if own else 'other resident'} RLS failed for {table}.")
            assert_denied(connection, f"delete from public.{table}")
            assert_denied(connection, f"update public.{table} set id=id")
        assert_denied(connection, "update public.water_households set owner_id=%s where owner_id=%s", (other["id"], owner["id"]))
        assert_denied(connection, "update public.water_ai_insights set input_hash=repeat('a',64)")
        assert_denied(connection, "update public.water_simulation_sessions set user_id=%s,payload='{}'::jsonb", (other["id"],))
        assert_denied(connection, "insert into public.water_households(owner_id) values(%s)", (actor["id"],))
        connection.execute("reset role")

    connection.execute("update public.profiles set account_status='suspended' where id=%s", (owner["id"],))
    connection.execute("set local role authenticated")
    connection.execute(
        "select set_config('request.jwt.claims',%s,true)",
        (json.dumps({"sub": owner["id"], "role": "authenticated"}),),
    )
    for table in TABLES:
        check(connection.execute(f"select count(*) n from public.{table}").fetchone()["n"] == 0,
              f"Suspended owner retained access to {table}.")
    connection.execute("reset role")
    connection.execute("update public.profiles set account_status='active' where id=%s", (owner["id"],))

    connection.execute("set local role anon")
    for table in TABLES:
        assert_denied(connection, f"select * from public.{table}")
    connection.execute("reset role")
    print("PASS: live PostgreSQL owner/other/suspended/anonymous RLS; direct client writes denied.")


def verify_database(connection) -> None:
    """Real SQL handlers; only identity/provider I/O are isolated for the fixture."""
    from unittest.mock import patch
    import os
    from fastapi.testclient import TestClient
    from backend.app import main, water
    from backend.app.security import current_user

    owner, other = fixture_owner(connection), fixture_owner(connection)

    @contextmanager
    def shared_connection():
        # API context-manager exits must never commit or close the outer transaction.
        yield connection

    previous = dict(main.app.dependency_overrides)
    try:
        with TemporaryDirectory(prefix="ecopulse-water-rollback-") as temporary:
            with patch.object(water, "postgres_connection", shared_connection), \
                 patch.object(main, "DB_PATH", Path(temporary) / "fixture.sqlite3"), \
                 patch.dict(os.environ, {"GEMINI_API_KEY": ""}), \
                 TestClient(main.app) as client:
                request(client, "GET", "/dashboard", status=401)
                main.app.dependency_overrides[current_user] = lambda: owner
                verify_api(connection, client, owner, other, main.app, current_user)
        verify_rls(connection, owner, other)
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


def verify_api(connection, client, owner: dict, other: dict, app, identity) -> None:
    from zoneinfo import ZoneInfo
    import psycopg

    def call(method, path, payload=None, *, status=200):
        # An intentionally rejected unique/FK write must not abort the fixture transaction.
        with connection.transaction(force_rollback=status >= 400):
            return request(client, method, path, payload, status=status)

    def notifications():
        return connection.execute(
            "select id::text,title,body from public.notifications where resident_id=%s order by id", (owner["id"],)
        ).fetchall()

    def actual_snapshot():
        result = {}
        for table in TABLES:
            if table == "water_simulation_sessions":
                continue
            predicate = "owner_id=%s" if table == "water_households" else (
                "household_id in (select id from public.water_households where owner_id=%s)"
            )
            result[table] = connection.execute(
                f"select to_jsonb(t) row from public.{table} t where {predicate} order by id", (owner["id"],)
            ).fetchall()
        result["notifications"] = notifications()
        result["incidents"] = connection.execute(
            "select id::text from public.incidents where reporter_id=%s", (owner["id"],)
        ).fetchall()
        return result

    household = call("GET", "/household")
    settings = {"name": "Rollback water household", "household_size": 4,
                "daily_target_litres": 600, "tariff_per_kl": 25, "timezone": "Africa/Johannesburg"}
    household = call("PUT", "/household", settings)
    check(float(household["daily_target_litres"]) == 600 and float(household["tariff_per_kl"]) == 25,
          "Household settings did not persist.")
    today = datetime.now(ZoneInfo(household["timezone"])).date()
    empty = call("GET", "/dashboard")
    check(empty["summary"]["today_litres"] is None and not notifications(),
          "Empty dates were reported as measured use or generated notifications.")
    call("POST", "/consumption", {"date": str(today), "litres": -1}, status=422)
    call("POST", "/consumption", {"date": str(today + timedelta(days=1)), "litres": 600}, status=422)
    call("PUT", "/household", {**settings, "timezone": "Unsupported/Timezone"}, status=422)

    for offset in range(6, 0, -1):
        call("POST", "/consumption", {"date": str(today - timedelta(days=offset)), "litres": 590}, status=201)
    daily = call("POST", "/consumption", {"date": str(today), "litres": 850}, status=201)
    dashboard = call("GET", "/dashboard")
    summary = dashboard["summary"]
    check(summary["today_litres"] == 850 and summary["yesterday_litres"] == 590,
          "Today/yesterday values did not use saved actual records.")
    check(summary["difference_litres"] == 250 and abs(summary["percentage_vs_target"] - 41.67) < .01,
          "The 850 L / 600 L target calculation is incorrect.")
    check(abs(summary["seven_day_average_litres"] - (850 + 6 * 590) / 7) < .01,
          "Seven-day average calculation is incorrect.")
    check(summary["efficiency_score"] is not None and 0 <= summary["efficiency_score"] <= 100,
          "Efficiency score is outside its documented range.")
    check(len(notifications()) == 7, "Daily reports did not create exactly one notification per available day.")
    call("POST", "/consumption", {"date": str(today), "litres": 850}, status=409)
    page = call("GET", "/consumption?limit=1&offset=1")
    check(page["total"] == 7 and len(page["items"]) == 1, "Actual consumption pagination failed.")

    call("PUT", "/consumption/" + daily["id"], {"date": str(today), "litres": 1400})
    current = call("GET", "/dashboard")
    check(current["summary"]["today_litres"] == 1400 and current["summary"]["status"] == "Possible Leak",
          "Corrected daily consumption did not produce the configured possible-leak screening.")
    alert = next((row for row in current["alerts"] if row["alert_type"] == "possible_leak"), None)
    check(alert is not None, "High consumption did not persist a possible-leak alert.")
    alert_count = call("GET", "/alerts")["total"]
    for _ in range(2):
        call("GET", "/dashboard")
    check(call("GET", "/alerts")["total"] == alert_count and len(notifications()) == 7,
          "Repeated reads or daily corrections duplicated alerts/notifications.")
    call("PATCH", "/alerts/" + alert["id"], {"status": "dismissed"})
    check(any(row["id"] == alert["id"] and row["status"] == "dismissed" for row in call("GET", "/alerts")["items"]),
          "Dismissed alert state was lost during recomputation.")
    print("PASS: real SQL daily CRUD, target/percentage/average math, alerts and idempotent existing notifications.")

    analysed = call("POST", "/ai/analyse", {"date": str(today)})
    check(analysed["source"] == "rules" and analysed["analysis"]["recommendations"],
          "Missing Gemini configuration did not produce usable rule advice.")
    cached = call("POST", "/ai/analyse", {"date": str(today)})
    check(cached["id"] == analysed["id"], "Unchanged actual evidence did not reuse its saved AI insight.")
    explained = call("POST", "/explanations", {"date": str(today), "reasons": ["visitors", "laundry"],
                                                "text": "Visitors and extra laundry were reported."})
    check(explained["insight"]["id"] != analysed["id"], "A new explanation reused stale AI advice.")
    updated = call("POST", "/explanations", {"date": str(today), "reasons": ["suspected_leak"],
                                              "text": "The visitors have left; the visible taps will be checked."})
    check(updated["explanation"]["id"] == explained["explanation"]["id"] and
          updated["insight"]["id"] != explained["insight"]["id"],
          "Updating an explanation duplicated it or retained stale advice.")
    stored = call("GET", "/insights?date=" + str(today))
    check(stored["explanation"]["text"] == updated["explanation"]["text"], "The revised household explanation was not saved.")
    call("POST", "/ai/analyse", {"date": str(today - timedelta(days=45))}, status=422)
    print("PASS: persisted rule fallback, cached insight reuse and explanation-driven cache invalidation.")

    meter_day = today - timedelta(days=20)
    def meter(offset, value, reset=False, hours=0, status=201):
        stamp = datetime.combine(meter_day + timedelta(days=offset), time.min, ZoneInfo(household["timezone"])) + timedelta(hours=hours)
        return call("POST", "/meter-readings", {"reading_at": stamp.isoformat(), "cumulative_kl": value,
                                                "is_reset": reset, "notes": "Synthetic rollback-only meter fixture"}, status=status)

    meter(0, 100)
    meter(2, 101.2)
    meter(2, 0.5, hours=12, status=422)
    meter(1, 102, status=422)
    meter(3, .1, reset=True)
    meter(4, .7)
    meter(4, .7, status=409)
    measured = call("GET", "/dashboard?start_date=" + str(meter_day) + "&end_date=" + str(meter_day + timedelta(days=4)))
    points = {row["date"]: row for row in measured["series"]}
    for offset in (0, 1):
        point = points[str(meter_day + timedelta(days=offset))]
        check(abs(point["litres"] - 600) < .01 and point["is_estimated"],
              "A multi-day meter difference was not allocated/labeled as an estimate.")
    check(points[str(meter_day + timedelta(days=2))]["litres"] is None,
          "A meter reset invented consumption for an unmeasured interval.")
    full_day = points[str(meter_day + timedelta(days=3))]
    check(abs(full_day["litres"] - 600) < .01 and not full_day["is_estimated"] and full_day["data_source"] == "meter",
          "The post-reset complete-day meter difference is incorrect.")
    check(call("GET", "/meter-readings?limit=1&offset=1")["total"] == 4, "Meter validation/pagination retained invalid or duplicate rows.")
    print("PASS: live cumulative-meter SQL, multi-day estimates, chronological validation and explicit reset baselines.")

    before = actual_snapshot()
    simulated = call("POST", "/simulation/generate", {"days": 30, "household_size": 4,
                                                        "daily_target_litres": 600, "scenario": "normal"}, status=201)
    session_id = simulated["session_id"]
    check(len(simulated["series"]) == 30 and simulated["is_simulation"], "Simulation did not generate thirty labeled days.")
    advanced = call("POST", "/simulation/next-day", {"session_id": session_id})
    check(len(advanced["series"]) == 31, "Simulate Next Day failed to append its record.")
    leaked = call("POST", "/simulation/trigger-leak", {"session_id": session_id})
    check(leaked["summary"]["today_litres"] >= 1440 and leaked["summary"]["status"] == "Possible Leak",
          "Trigger Water Leak failed to recalculate simulation cards and screening.")
    simulated_date = leaked["as_of_date"]
    sim_advice = call("POST", "/ai/analyse", {"session_id": session_id, "date": simulated_date})
    check(sim_advice["source"] == "rules" and "simulat" in sim_advice["analysis"]["caveat"].lower(),
          "Simulation fallback did not disclose that the evidence was synthetic.")
    sim_explanation = call("POST", "/explanations", {"session_id": session_id, "date": simulated_date,
                                                    "reasons": ["laundry"], "text": "Synthetic extra laundry."})
    check(sim_explanation["insight"]["id"] != sim_advice["id"], "Simulation explanation did not invalidate cached analysis.")
    sim_alert = next(row for row in call("GET", "/alerts?session_id=" + session_id)["items"] if row["alert_type"] == "possible_leak")
    call("PATCH", "/alerts/" + sim_alert["id"] + "?session_id=" + session_id, {"status": "dismissed"})
    check(actual_snapshot() == before, "Simulation modified actual household records, notifications, alerts, insights or incidents.")

    app.dependency_overrides[identity] = lambda: other
    check(call("GET", "/consumption")["total"] == 0, "Another resident could list the owner's daily records.")
    call("PUT", "/consumption/" + daily["id"], {"date": str(today), "litres": 1}, status=404)
    call("PATCH", "/alerts/" + alert["id"], {"status": "read"}, status=404)
    call("GET", "/dashboard?session_id=" + session_id, status=404)
    call("POST", "/simulation/next-day", {"session_id": session_id}, status=404)
    call("POST", "/simulation/reset", {"session_id": session_id}, status=404)
    call("POST", "/ai/analyse", {"session_id": session_id, "date": simulated_date}, status=404)
    other_household = call("GET", "/household")
    app.dependency_overrides[identity] = lambda: owner
    reset = call("POST", "/simulation/reset", {"session_id": session_id})
    check(reset["reset"] and actual_snapshot() == before, "Reset changed real data or failed to delete its own session.")
    check(call("GET", "/simulation") is None, "Reset simulation remained available.")
    # Retain one separate owned fixture so raw RLS also covers simulation rows.
    call("POST", "/simulation/generate", {"days": 7, "scenario": "normal"}, status=201)

    try:
        with connection.transaction():
            connection.execute("""insert into public.water_consumption_explanations
                (household_id,consumption_id,consumption_date) values(%s,%s,%s)""",
                (other_household["id"], daily["id"], today - timedelta(days=80)))
        raise VerificationFailure("A cross-household consumption foreign key was accepted.")
    except psycopg.errors.ForeignKeyViolation:
        pass
    print("PASS: isolated simulation/analysis/explanations/alerts/reset, cross-owner API denial and composite household foreign keys.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    operations = parser.add_mutually_exclusive_group(required=True)
    operations.add_argument("--verify", action="store_true", help="Apply schema and exercise fixtures, always roll back")
    operations.add_argument("--apply-schema", action="store_true", help="Commit the additive migration only; no fixture data")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / "backend/.env")
    from backend.app.security import postgres_connection

    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with postgres_connection() as connection:
            with connection.transaction(force_rollback=args.verify):
                connection.execute("set local statement_timeout='30s'")
                connection.execute("set local lock_timeout='5s'")
                apply_schema(connection)
                if args.verify:
                    # Applying twice also verifies the migration is safe to replay.
                    apply_schema(connection)
                    verify_database(connection)
        print("PASS: all Smart Water fixtures and schema changes rolled back." if args.verify else
              "Smart Water additive migration committed; no demo records were created.")
        return 0
    except Exception as exc:
        print(f"Smart Water {'verification' if args.verify else 'schema application'} failed ({type(exc).__name__}). No partial transaction committed.")
        if isinstance(exc, VerificationFailure):
            print(str(exc))
        return 1
    finally:
        logging.disable(previous_logging)


if __name__ == "__main__":
    raise SystemExit(main())

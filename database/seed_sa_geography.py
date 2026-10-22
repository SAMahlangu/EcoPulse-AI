"""Validate and import the committed MDB South Africa geography snapshot.

Run after migration 006 with backend/.env configured:
    python database/seed_sa_geography.py

Validation only (no database connection):
    python database/seed_sa_geography.py --validate-only
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT = ROOT / "database" / "seeds" / "sa_municipalities_mdb_2026.json"
EXPECTED_PROVINCES = {
    "EC": "Eastern Cape",
    "FS": "Free State",
    "GP": "Gauteng",
    "KZN": "KwaZulu-Natal",
    "LP": "Limpopo",
    "MP": "Mpumalanga",
    "NC": "Northern Cape",
    "NW": "North West",
    "WC": "Western Cape",
}
EXPECTED_COUNTS = {"metropolitan": 8, "district": 44, "local": 205}
REFERENCE_SOURCE = "Municipal Demarcation Board (MDB), 2026 municipal layers"


def load_and_validate(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("source") != REFERENCE_SOURCE:
        raise ValueError("Snapshot source metadata is missing or unexpected")
    if not data.get("retrieved_on") or not data.get("effective_from"):
        raise ValueError("Snapshot retrieval and effective dates are required")
    try:
        date.fromisoformat(data["retrieved_on"])
        date.fromisoformat(data["effective_from"])
    except ValueError as exc:
        raise ValueError("Snapshot dates must use YYYY-MM-DD format") from exc
    rows = data.get("municipalities")
    if not isinstance(rows, list):
        raise ValueError("Snapshot must contain a municipalities array")

    codes = [str(row.get("code", "")).strip() for row in rows]
    if len(rows) != 257 or len(set(codes)) != 257 or any(not code for code in codes):
        raise ValueError(f"Expected 257 rows with unique non-empty MDB codes; found {len(rows)} rows")

    counts = {kind: sum(row.get("type") == kind for row in rows) for kind in EXPECTED_COUNTS}
    if counts != EXPECTED_COUNTS:
        raise ValueError(f"Unexpected municipality type totals: {counts}")
    if set(row.get("province_code") for row in rows) != set(EXPECTED_PROVINCES):
        raise ValueError("Municipalities must cover exactly the nine configured provinces")

    by_code = {row["code"]: row for row in rows}
    for row in rows:
        if not row.get("name", "").strip():
            raise ValueError(f"Municipality {row.get('code')} has no name")
        if row["type"] in ("district", "metropolitan") and row.get("parent_code") is not None:
            raise ValueError(f"{row['code']} cannot have a parent")
        if row["type"] == "local":
            parent = by_code.get(row.get("parent_code"))
            if not parent or parent["type"] != "district":
                raise ValueError(f"Local municipality {row['code']} has an invalid district parent")
            if parent["province_code"] != row["province_code"]:
                raise ValueError(f"Local municipality {row['code']} and its district are in different provinces")

    return data


def seed_database(data: dict[str, Any]) -> None:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("Install backend requirements to run the database importer") from exc

    config = dotenv_values(ROOT / "backend" / ".env")
    url = os.getenv("DATABASE_URL") or config.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is missing; configure backend/.env first")

    province_ids: dict[str, str] = {}
    municipality_ids: dict[str, str] = {}
    source_date = date.fromisoformat(data["effective_from"])
    with psycopg.connect(url, row_factory=dict_row) as connection:
        for code, name in EXPECTED_PROVINCES.items():
            row = connection.execute(
                """insert into public.provinces (code, name)
                   values (%s, %s)
                   on conflict (code) do update set name = excluded.name
                   returning id::text""",
                (code, name),
            ).fetchone()
            province_ids[code] = row["id"]

        municipalities = data["municipalities"]
        roots = [row for row in municipalities if row["type"] != "local"]
        locals_ = [row for row in municipalities if row["type"] == "local"]

        def upsert(row: dict[str, Any], parent_id: str | None) -> str:
            result = connection.execute(
                """insert into public.municipalities
                     (province, province_id, name, code, municipality_type,
                      parent_municipality_id, is_demonstration, is_onboarded,
                      reference_source, reference_as_of)
                   values (%s, %s, %s, %s, %s, %s, false, false, %s, %s)
                   on conflict (code) do update set
                     province = excluded.province,
                     province_id = excluded.province_id,
                     name = excluded.name,
                     municipality_type = excluded.municipality_type,
                     parent_municipality_id = excluded.parent_municipality_id,
                     reference_source = excluded.reference_source,
                     reference_as_of = excluded.reference_as_of
                   returning id::text""",
                (
                    EXPECTED_PROVINCES[row["province_code"]],
                    province_ids[row["province_code"]],
                    row["name"],
                    row["code"],
                    row["type"],
                    parent_id,
                    REFERENCE_SOURCE,
                    source_date,
                ),
            ).fetchone()
            return result["id"]

        # Districts and metros first so all local parent references resolve.
        for row in roots:
            municipality_ids[row["code"]] = upsert(row, None)
        for row in locals_:
            parent_id = municipality_ids.get(row["parent_code"])
            if parent_id is None:
                raise ValueError(f"Parent district {row['parent_code']} was not imported")
            municipality_ids[row["code"]] = upsert(row, parent_id)

        connection.execute(
            """update public.profiles profile
               set province_id = municipality.province_id,
                   province = municipality.province
               from public.municipalities municipality
               where profile.municipality_id = municipality.id
                 and municipality.province_id is not null
                 and (profile.province_id is distinct from municipality.province_id
                      or profile.province is distinct from municipality.province)"""
        )

    print("Imported 9 provinces and 257 official MDB municipality references.")
    print("Existing municipality UUIDs and platform onboarding status were preserved.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    data = load_and_validate(args.snapshot)
    print(f"Validated {len(data['municipalities'])} municipalities from {data['source']} ({data['retrieved_on']}).")
    if not args.validate_only:
        seed_database(data)


if __name__ == "__main__":
    main()

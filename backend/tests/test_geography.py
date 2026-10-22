from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.security import current_user
from backend.app import workspace
from database.seed_sa_geography import DEFAULT_SNAPSHOT, load_and_validate


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.app.main.DB_PATH", tmp_path / "geography-test.sqlite3")
    monkeypatch.setenv("ECOPULSE_LOCAL_DEMO", "true")
    with TestClient(app) as test_client:
        yield test_client


def test_mdb_snapshot_has_all_257_unique_codes_and_valid_hierarchy():
    data = load_and_validate(DEFAULT_SNAPSHOT)
    municipalities = data["municipalities"]

    assert len(municipalities) == 257
    assert len({row["code"] for row in municipalities}) == 257
    assert {row["province_code"] for row in municipalities} == {
        "EC", "FS", "GP", "KZN", "LP", "MP", "NC", "NW", "WC",
    }
    assert sum(row["type"] == "metropolitan" for row in municipalities) == 8
    assert sum(row["type"] == "district" for row in municipalities) == 44
    assert sum(row["type"] == "local" for row in municipalities) == 205
    assert data["effective_from"] == "2026-11-04"
    gauteng = [row for row in municipalities if row["province_code"] == "GP"]
    assert len(gauteng) == 11
    assert all(row["province_code"] == "GP" for row in gauteng)


def test_public_province_dropdown_lists_all_nine(client: TestClient, monkeypatch):
    provinces = [
        {"id": f"00000000-0000-0000-0000-{number:012d}", "name": name, "code": code}
        for number, (code, name) in enumerate([
            ("EC", "Eastern Cape"), ("FS", "Free State"), ("GP", "Gauteng"),
            ("KZN", "KwaZulu-Natal"), ("LP", "Limpopo"), ("MP", "Mpumalanga"),
            ("NC", "Northern Cape"), ("NW", "North West"), ("WC", "Western Cape"),
        ], start=1)
    ]
    monkeypatch.setattr(workspace, "_execute", lambda *args, **kwargs: provinces)

    response = client.get("/api/v1/locations/provinces")

    assert response.status_code == 200
    assert len(response.json()["items"]) == 9
    assert {item["code"] for item in response.json()["items"]} == {"EC", "FS", "GP", "KZN", "LP", "MP", "NC", "NW", "WC"}


def test_municipality_list_filters_by_province_and_paginates(client: TestClient, monkeypatch):
    province_id = UUID("00000000-0000-0000-0000-000000000001")
    calls = []

    def execute(sql, params=(), *, fetch="all"):
        calls.append((sql, params, fetch))
        if fetch == "one" and "count(*)" in sql:
            return {"total": 1}
        return [{
            "id": "00000000-0000-0000-0000-000000000101",
            "province_id": str(province_id),
            "province": "Gauteng",
            "name": "City of Johannesburg",
            "code": "JHB",
            "municipality_type": "metropolitan",
            "parent_municipality_id": None,
            "parent_municipality": None,
            "is_onboarded": False,
        }]

    monkeypatch.setattr(workspace, "_execute", execute)
    response = client.get(f"/api/v1/locations/municipalities?province_id={province_id}&limit=25&offset=10")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["limit"] == 25
    assert response.json()["offset"] == 10
    assert calls[0][1] == (province_id,)
    assert calls[1][1] == (province_id, 25, 10)
    assert "public.municipalities_import" not in calls[1][0]
    assert "from public.municipalities m" in calls[1][0]
    assert "m.is_demonstration is false" in calls[1][0]
    assert "reference_verified" not in calls[1][0]
    assert "reference_active" not in calls[1][0]


def test_location_endpoints_reject_malformed_uuids(client: TestClient):
    assert client.get("/api/v1/locations/municipalities/not-a-uuid").status_code == 422
    assert client.get("/api/v1/locations/municipalities?province_id=not-a-uuid").status_code == 422
    assert client.get("/api/v1/locations/communities?municipality_id=not-a-uuid").status_code == 422


def test_municipality_detail_and_community_options_are_public_and_scoped(client: TestClient, monkeypatch):
    municipality_id = UUID("00000000-0000-0000-0000-000000000002")
    calls = []

    def execute(sql, params=(), *, fetch="all"):
        calls.append((sql, params, fetch))
        if fetch == "one" and "count(*)" in sql:
            return {"total": 1}
        municipality = {
            "id": str(municipality_id), "province_id": "00000000-0000-0000-0000-000000000001",
            "province": "Gauteng", "name": "City of Johannesburg", "code": "JHB",
            "municipality_type": "metropolitan", "parent_municipality_id": None,
            "parent_municipality": None, "is_onboarded": False,
        }
        if fetch == "one":
            return municipality
        if "from public.communities c" in sql and "count(*)" not in sql:
            return [{"id": "00000000-0000-0000-0000-000000000003", "municipality_id": str(municipality_id), "name": "Hatfield"}]
        return [municipality]

    monkeypatch.setattr(workspace, "_execute", execute)
    detail = client.get(f"/api/v1/locations/municipalities/{municipality_id}")
    communities = client.get(f"/api/v1/locations/communities?municipality_id={municipality_id}")

    assert detail.status_code == 200
    assert detail.json()["code"] == "JHB"
    assert communities.status_code == 200
    assert communities.json()["items"][0]["municipality_id"] == str(municipality_id)
    assert calls[-2][1] == (municipality_id,)
    assert calls[-1][1] == (municipality_id, 100, 0)


def test_resident_onboarding_saves_database_ids_and_validates_province(client: TestClient, monkeypatch):
    province_id = UUID("00000000-0000-0000-0000-000000000001")
    municipality_id = UUID("00000000-0000-0000-0000-000000000002")
    community_id = UUID("00000000-0000-0000-0000-000000000003")
    saved = {}

    def execute(sql, params=(), *, fetch="all"):
        if sql.startswith("select province,province_id from public.municipalities"):
            return {"province": "Gauteng", "province_id": province_id}
        if sql.startswith("select 1 from public.communities"):
            return {"exists": 1}
        if sql.startswith("with saved as ("):
            saved["params"] = params
            return {"province_id": str(province_id), "province": "Gauteng", "municipality_id": str(municipality_id), "community_id": str(community_id), "suburb": "Hatfield", "notification_preferences": {}, "onboarding_completed_at": "2026-10-22T00:00:00Z"}
        raise AssertionError(f"Unexpected query: {sql}")

    monkeypatch.setattr(workspace, "_execute", execute)
    monkeypatch.setitem(app.dependency_overrides, current_user, lambda: {"id": "resident-id", "role": "resident"})
    response = client.put("/api/v1/resident/onboarding", json={
        "province_id": str(province_id),
        "municipality_id": str(municipality_id),
        "community_id": str(community_id),
        "suburb": "Hatfield",
        "notification_preferences": {"climate_alerts": True},
    })

    assert response.status_code == 200
    assert response.json()["province_id"] == str(province_id)
    assert response.json()["municipality_id"] == str(municipality_id)
    assert response.json()["community_id"] == str(community_id)
    assert saved["params"][0] == "Gauteng"
    assert saved["params"][1] == municipality_id
    assert saved["params"][2] == community_id


def test_resident_cannot_save_municipality_under_wrong_province(client: TestClient, monkeypatch):
    monkeypatch.setattr(workspace, "_execute", lambda *args, **kwargs: None)
    monkeypatch.setitem(app.dependency_overrides, current_user, lambda: {"id": "resident-id", "role": "resident"})

    response = client.put("/api/v1/resident/onboarding", json={
        "province_id": "00000000-0000-0000-0000-000000000001",
        "municipality_id": "00000000-0000-0000-0000-000000000002",
        "suburb": "Hatfield",
        "notification_preferences": {},
    })

    assert response.status_code == 422


def test_resident_role_cannot_access_admin_municipality_route(client: TestClient, monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, current_user, lambda: {"id": "resident-id", "role": "resident"})
    response = client.post("/api/v1/admin/municipalities", json={"municipality_id": "00000000-0000-0000-0000-000000000001"})
    assert response.status_code == 403


def test_admin_import_review_is_province_filtered_and_never_public(client: TestClient, monkeypatch):
    province_id = UUID("00000000-0000-0000-0000-000000000001")
    calls = []

    def execute(sql, params=(), *, fetch="all"):
        calls.append((sql, params, fetch))
        if fetch == "one":
            return {"total": 1}
        return [{
            "code": "BUF", "name": "Buffalo City", "province_id": str(province_id),
            "source_is_active": None, "verification_status": "pending",
            "confirmed_active": None, "existing_municipality_id": None,
        }]

    monkeypatch.setattr(workspace, "_execute", execute)
    monkeypatch.setitem(app.dependency_overrides, current_user, lambda: {"id": "admin-id", "role": "admin"})
    response = client.get(f"/api/v1/admin/municipality-imports?province_id={province_id}")

    assert response.status_code == 200
    assert response.json()["items"][0]["source_is_active"] is None
    assert calls[0][1] == (province_id,)
    assert calls[1][1] == (province_id, 100, 0)
    assert "municipality_import_review" in calls[1][0]

    monkeypatch.setitem(app.dependency_overrides, current_user, lambda: {"id": "resident-id", "role": "resident"})
    denied = client.get(f"/api/v1/admin/municipality-imports?province_id={province_id}")
    assert denied.status_code == 403


def test_admin_verification_requires_note_and_audits_decision(client: TestClient, monkeypatch):
    statements = []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=()):
            statements.append((sql, params))
            if sql.lstrip().startswith("update public.municipality_import_review"):
                return self
            if sql.lstrip().startswith("insert into public.admin_audit_logs"):
                return self
            raise AssertionError(f"Unexpected query: {sql}")

        def fetchone(self):
            return {"code": "BUF", "name": "Buffalo City", "verification_status": "verified", "confirmed_active": True}

    monkeypatch.setattr(workspace, "postgres_connection", FakeConnection)
    monkeypatch.setitem(app.dependency_overrides, current_user, lambda: {"id": "admin-id", "role": "admin"})

    response = client.put("/api/v1/admin/municipality-imports/BUF/verification", json={
        "confirmed_active": True,
        "verification_note": "Checked against a current official source on 2026-10-22.",
    })
    assert response.status_code == 200
    assert response.json()["confirmed_active"] is True
    assert len(statements) == 2
    assert statements[0][1][0] is True

    invalid = client.put("/api/v1/admin/municipality-imports/BUF/verification", json={
        "confirmed_active": True,
        "verification_note": "  short  ",
    })
    assert invalid.status_code == 422


def test_admin_onboards_existing_reference_without_creating_duplicate(client: TestClient, monkeypatch):
    municipality_id = "00000000-0000-0000-0000-000000000123"
    statements = []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=()):
            statements.append(sql)
            if sql.startswith("select id::text,name,province"):
                return self
            if sql.startswith("update public.municipalities set is_onboarded=true"):
                return self
            if sql.startswith("insert into public.admin_audit_logs"):
                return self
            raise AssertionError(f"Unexpected query: {sql}")

        def fetchone(self):
            return {
                "id": municipality_id, "name": "City of Johannesburg", "province": "Gauteng",
                "province_id": "00000000-0000-0000-0000-000000000001", "code": "JHB",
                "municipality_type": "metropolitan", "parent_municipality_id": None,
                "is_demonstration": False, "is_onboarded": False, "created_at": "2026-10-22T00:00:00Z",
            }

    monkeypatch.setattr(workspace, "postgres_connection", FakeConnection)
    monkeypatch.setitem(app.dependency_overrides, current_user, lambda: {"id": "admin-id", "role": "admin"})
    response = client.post("/api/v1/admin/municipalities", json={"municipality_id": municipality_id})

    assert response.status_code == 200
    assert response.json()["id"] == municipality_id
    assert response.json()["is_onboarded"] is True
    assert any(statement.startswith("update public.municipalities") for statement in statements)
    assert not any(statement.startswith("insert into public.municipalities") for statement in statements)


def test_municipal_dashboard_queries_only_assigned_municipality_ids(client: TestClient, monkeypatch):
    assigned = ["00000000-0000-0000-0000-000000000123"]
    calls = []

    def execute(sql, params=(), *, fetch="all"):
        calls.append(params)
        if fetch == "one":
            return {"total": 0, "new": 0, "under_review": 0, "in_progress": 0, "resolved": 0, "high_priority": 0}
        return []

    monkeypatch.setattr(workspace, "_execute", execute)
    monkeypatch.setitem(app.dependency_overrides, current_user, lambda: {"id": "municipal-id", "role": "municipal", "municipality_ids": assigned})
    response = client.get("/api/v1/municipal/dashboard")

    assert response.status_code == 200
    assert calls == [(assigned,), (assigned,)]
    assert response.json()["municipality_ids"] == assigned


def test_community_dropdown_requires_municipality_uuid(client: TestClient):
    assert client.get("/api/v1/locations/communities").status_code == 422


@pytest.mark.parametrize("province", ["Gauteng", "Western Cape", "Gauteng' OR 1=1 --"])
def test_canonical_province_filter_is_bound_without_activity_checks(client, monkeypatch, province):
    calls = []
    def execute(sql, params=(), *, fetch="all"):
        calls.append((sql, params))
        if fetch == "one":
            return {"total": 1}
        return [{"code": "HIST", "name": "Historical reference", "province": province,
                 "category": "B", "id": "00000000-0000-0000-0000-000000000002"}]
    monkeypatch.setattr(workspace, "_execute", execute)
    response = client.get("/api/v1/locations/municipalities", params={"province": province})
    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == "00000000-0000-0000-0000-000000000002"
    assert calls[0][1] == (province,)
    assert calls[1][1] == (province, 100, 0)
    assert province not in calls[1][0]
    assert 'lower(p.name) = lower(%s)' in calls[1][0]
    assert 'p.id=m.province_id' in calls[1][0]
    assert 'municipalities_import' not in calls[1][0]
    assert "reference_verified" not in calls[1][0]
    assert "reference_active" not in calls[1][0]
    assert "m.is_demonstration is false" in calls[1][0]


def test_codes_cannot_enter_uuid_onboarding_fields(client, monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, current_user, lambda: {"id": "resident-id", "role": "resident"})
    response = client.put("/api/v1/resident/onboarding", json={
        "province_id": "00000000-0000-0000-0000-000000000001",
        "municipality_id": "JHB", "suburb": "Example suburb"})
    assert response.status_code == 422


def test_accepting_invitation_assigns_profile_province_and_scoped_membership(client, monkeypatch):
    statements = []
    municipality_id = "00000000-0000-0000-0000-000000000002"
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def execute(self, sql, params=()):
            statements.append((sql, params));self.sql = sql;return self
        def fetchone(self):
            if "from public.municipal_invitations" in self.sql:
                return {"id": "invitation-id", "municipality_id": municipality_id}
            return {"id": "official-id"}
    monkeypatch.setattr(workspace, "postgres_connection", Connection)
    monkeypatch.setitem(app.dependency_overrides, current_user, lambda: {"id": "official-id", "role": "resident", "email": "official@example.test"})
    response = client.post("/api/v1/auth/accept-municipal-invitation", json={"token": "x" * 40})
    assert response.status_code == 200
    assert "province_id=m.province_id,province=m.province" in statements[1][0]
    assert statements[1][1] == (municipality_id, "official-id")
    assert "insert into public.municipal_memberships" in statements[2][0]
    assert statements[2][1] == ("official-id", municipality_id)
    assert response.json()["role"] == "municipal"

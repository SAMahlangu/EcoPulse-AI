from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app import workspace
from backend.app.security import current_user

PROVINCE = "00000000-0000-0000-0000-000000000001"
MUNICIPALITY = "00000000-0000-0000-0000-000000000002"
OFFICIAL = "00000000-0000-0000-0000-000000000003"
PAYLOAD = {"full_name": "Test Official", "email": "Official@example.test", "password": "Test-only-Pass-987!",
           "province_id": PROVINCE, "municipality_id": MUNICIPALITY}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    state = {"role": "admin", "match": True, "duplicate": False, "fail_save": False,
             "cleanup_fail": False, "auth_status": 200, "sql": [], "http": []}
    municipality = {"id": MUNICIPALITY, "name": "Test municipality", "province": "Gauteng", "province_id": PROVINCE}
    def execute(sql, params=(), *, fetch="all"):
        state["sql"].append((sql, params))
        if "from auth.users" in sql:
            return {"id": "existing-user"} if state["duplicate"] else None
        return municipality if state["match"] else None
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def execute(self, sql, params=()):
            state["sql"].append((sql, params))
            if state["fail_save"] and "insert into public.municipal_memberships" in sql:
                raise RuntimeError("Database write failed")
            return self
        def fetchone(self): return municipality if state["match"] else None
    class Response:
        def __init__(self, status, value): self.status_code=status;self.value=value
        def json(self): return self.value
    class AuthClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, url, **kwargs):
            state["http"].append(("POST", url, kwargs))
            return Response(state["auth_status"], {"id": OFFICIAL, "error_code": "email_exists"})
        async def delete(self, url, **kwargs):
            state["http"].append(("DELETE", url, kwargs))
            return Response(500 if state["cleanup_fail"] else 200, {})
    monkeypatch.setattr("backend.app.main.DB_PATH", tmp_path / "officials.sqlite3")
    monkeypatch.setenv("ECOPULSE_LOCAL_DEMO", "true")
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "server-test-only")
    monkeypatch.setattr(workspace, "_execute", execute)
    monkeypatch.setattr(workspace, "postgres_connection", Connection)
    monkeypatch.setattr(workspace.httpx, "AsyncClient", AuthClient)
    monkeypatch.setitem(app.dependency_overrides, current_user, lambda: {"id": "admin-id", "role": state["role"]})
    with TestClient(app) as client:
        yield client, state


def test_admin_creates_password_account_with_scoped_membership(setup):
    client, state = setup
    response = client.post("/api/v1/admin/municipal-officials", json=PAYLOAD)
    assert response.status_code == 201
    assert response.json()["id"] == OFFICIAL
    assert response.json()["role"] == "municipal"
    assert response.json()["municipality_id"] == MUNICIPALITY
    assert response.json()["province_id"] == PROVINCE
    assert PAYLOAD["password"] not in response.text
    assert "password" not in response.json()
    assert len(state["http"]) == 1
    method, url, options = state["http"][0]
    assert method == "POST" and url.endswith("/auth/v1/admin/users")
    assert options["json"]["password"] == PAYLOAD["password"]
    assert options["json"]["email_confirm"] is True
    assert options["json"]["user_metadata"] == {"full_name": "Test Official"}
    assert options["json"]["email"] == "official@example.test"
    assert state["sql"][0][1] == (UUID(MUNICIPALITY), UUID(PROVINCE))
    assert "reference_verified" not in state["sql"][0][0]
    assert "reference_active" not in state["sql"][0][0]
    profile = next(params for sql, params in state["sql"] if "insert into public.profiles" in sql)
    assert profile == (UUID(OFFICIAL), "Test Official", UUID(MUNICIPALITY), "Gauteng")
    membership = next(params for sql, params in state["sql"] if "insert into public.municipal_memberships" in sql)
    assert membership == (UUID(OFFICIAL), UUID(MUNICIPALITY))
    assert all(PAYLOAD["password"] not in str(params) for sql, params in state["sql"])


@pytest.mark.parametrize("role", ["resident", "municipal"])
def test_non_admin_cannot_create_official(setup, role):
    client, state = setup;state["role"] = role
    assert client.post("/api/v1/admin/municipal-officials", json=PAYLOAD).status_code == 403
    assert not state["http"] and not state["sql"]


@pytest.mark.parametrize("field", ["match", "duplicate"])
def test_invalid_location_or_existing_account_is_rejected_before_auth_creation(setup, field):
    client, state = setup;state[field] = field == "duplicate"
    response = client.post("/api/v1/admin/municipal-officials", json=PAYLOAD)
    assert response.status_code == (409 if field == "duplicate" else 422)
    assert not state["http"]


@pytest.mark.parametrize("cleanup_fail", [False, True])
def test_failed_membership_save_cleans_up_only_new_account(setup, cleanup_fail):
    client, state = setup;state["fail_save"] = True;state["cleanup_fail"] = cleanup_fail
    response = client.post("/api/v1/admin/municipal-officials", json=PAYLOAD)
    assert response.status_code == 503
    assert state["http"][-1][0] == "DELETE"
    assert state["http"][-1][1].endswith(f"/admin/users/{OFFICIAL}")
    assert ("cleanup could not finish" in response.json()["detail"]) == cleanup_fail
    assert PAYLOAD["password"] not in response.text


def test_concurrent_email_collision_does_not_modify_existing_account(setup):
    client, state = setup;state["auth_status"] = 422
    assert client.post("/api/v1/admin/municipal-officials", json=PAYLOAD).status_code == 409
    assert len(state["http"]) == 1
    assert not any("insert into" in sql for sql, params in state["sql"])


def test_short_password_and_non_uuid_municipality_are_rejected(setup):
    client, state = setup
    invalid = client.post("/api/v1/admin/municipal-officials", json={**PAYLOAD, "password": "short"})
    assert invalid.status_code == 422
    assert "short" not in invalid.text
    assert client.post("/api/v1/admin/municipal-officials", json={**PAYLOAD, "municipality_id": "JHB"}).status_code == 422
    assert not state["http"]


def test_password_is_excluded_from_model_dump_and_repr():
    form = workspace.MunicipalOfficialRequest(**PAYLOAD)
    assert "password" not in form.model_dump()
    assert PAYLOAD["password"] not in repr(form)

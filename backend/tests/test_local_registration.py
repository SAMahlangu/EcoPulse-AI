from uuid import UUID
import pytest
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app import workspace

RESIDENT = "00000000-0000-0000-0000-000000000003"
PAYLOAD = {"full_name":"Test Resident", "email":"Resident@example.test", "password":"Test-only-pass-789!"}

@pytest.fixture
def setup(tmp_path,monkeypatch):
    state={"requests":[],"role":"resident","status":200}
    class Response:
        def __init__(self,status,value):self.status_code=status;self.value=value
        def json(self):return self.value
        def raise_for_status(self):
            if self.status_code>=400:raise RuntimeError("test HTTP failure")
    class AuthClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):return False
        async def post(self,url,**kwargs):
            state["requests"].append(("POST",url,kwargs))
            return Response(state["status"],{"id":RESIDENT,"error_code":"email_exists"})
        async def delete(self,url,**kwargs):
            state["requests"].append(("DELETE",url,kwargs));return Response(200,{})
    monkeypatch.setenv("ECOPULSE_LOCAL_PASSWORD_SIGNUP","true")
    monkeypatch.setenv("APP_PUBLIC_URL","http://localhost:5173")
    monkeypatch.setenv("SUPABASE_URL","https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY","server-only-test")
    monkeypatch.setattr("backend.app.main.DB_PATH",tmp_path/'local-signup.sqlite3')
    monkeypatch.setattr(workspace.httpx,"AsyncClient",AuthClient)
    monkeypatch.setattr(workspace,"_execute",lambda *args,**kwargs:{"role":state["role"]})
    with TestClient(app,base_url="http://localhost",client=("127.0.0.1",50000)) as client:yield client,state


def test_local_resident_creation_is_confirmed_without_assigning_privileged_role(setup):
    client,state=setup
    r=client.post('/api/v1/auth/register-local',json=PAYLOAD)
    assert r.status_code==201
    assert r.json()=={"id":RESIDENT,"requires_email_confirmation":False}
    body=state["requests"][0][2]["json"]
    assert body["email_confirm"] is True
    assert body["email"]=="resident@example.test"
    assert body["user_metadata"]=={"full_name":"Test Resident"}
    assert "role" not in body
    assert len(state["requests"])==1
    assert PAYLOAD["password"] not in r.text


def test_local_signup_is_disabled_by_default(setup,monkeypatch):
    client,state=setup;monkeypatch.delenv("ECOPULSE_LOCAL_PASSWORD_SIGNUP")
    assert client.post('/api/v1/auth/register-local',json=PAYLOAD).status_code==404
    assert not state['requests']


def test_remote_requests_are_rejected(setup):
    _,state=setup
    with TestClient(app,base_url="http://localhost",client=("203.0.113.1",50000)) as client:
        assert client.post('/api/v1/auth/register-local',json=PAYLOAD).status_code==403
    assert not state['requests']


def test_local_signup_stays_disabled_when_the_site_is_deployed(setup,monkeypatch):
    client,state=setup
    monkeypatch.setenv("APP_PUBLIC_URL","https://ecopulse.example")
    assert client.post('/api/v1/auth/register-local',json=PAYLOAD).status_code==404
    assert not state['requests']


def test_existing_account_is_not_overwritten(setup):
    client,state=setup;state['status']=422
    assert client.post('/api/v1/auth/register-local',json=PAYLOAD).status_code==409
    assert len(state['requests'])==1


def test_role_injection_and_short_password_are_rejected_without_echoing_secrets(setup):
    client,state=setup
    assert client.post('/api/v1/auth/register-local',json={**PAYLOAD,"role":"admin"}).status_code==422
    r=client.post('/api/v1/auth/register-local',json={**PAYLOAD,"password":"short"})
    assert r.status_code==422 and 'short' not in r.text
    assert not state['requests']


def test_failed_resident_profile_removes_only_new_account(setup):
    client,state=setup;state['role']=None
    assert client.post('/api/v1/auth/register-local',json=PAYLOAD).status_code==503
    assert state['requests'][-1][0]=='DELETE'
    assert state['requests'][-1][1].endswith('/'+RESIDENT)

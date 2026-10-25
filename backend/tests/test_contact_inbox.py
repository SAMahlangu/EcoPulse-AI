from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app import workspace
from backend.app.security import current_user


def test_contact_submission_is_available_to_admin(monkeypatch):
    workspace._contact_attempts.clear()
    stored = []
    def execute(sql, params=(), *, fetch='all'):
        if sql.startswith('insert into public.contact_enquiries'):
            assert sql.endswith('returning id')
            assert fetch == 'one'
            stored.append(dict(zip(('full_name','email','organisation','subject','message'), params), id='enquiry-id', created_at='2026-10-24T10:00:00Z'))
            return {'id': 'enquiry-id'}
        if 'count(*)' in sql:
            return {'total':len(stored)}
        return stored
    monkeypatch.setattr(workspace, '_execute', execute)
    client = TestClient(app)
    response = client.post('/api/v1/public/contact', json={'full_name':'Test Visitor','email':'visitor@example.com','subject':'Community access','message':'Please explain community access.'})
    assert response.status_code == 202
    assert response.json()['accepted']
    app.dependency_overrides[current_user] = lambda: {'id':'admin-id','role':'admin'}
    try:
        inbox = client.get('/api/v1/admin/enquiries')
        assert inbox.status_code == 200
        assert inbox.json()['items'][0]['message'] == 'Please explain community access.'
        assert inbox.json()['total'] == 1
    finally:
        app.dependency_overrides.clear()


def test_inbox_is_not_public_or_resident_accessible():
    client = TestClient(app)
    assert client.get('/api/v1/admin/enquiries').status_code == 401
    app.dependency_overrides[current_user] = lambda: {'id':'resident-id','role':'resident'}
    try:
        assert client.get('/api/v1/admin/enquiries').status_code == 403
    finally:
        app.dependency_overrides.clear()

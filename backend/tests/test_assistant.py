import httpx
import pytest
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.security import current_user
from backend.app import assistant


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-secret')
    app.dependency_overrides[current_user] = lambda: {'id': 'resident', 'role': 'resident'}
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_question_calls_gemini_with_history(client, monkeypatch):
    def request(method, url, **kwargs):
        assert method == 'POST' and url.endswith(':generateContent')
        assert kwargs['headers']['x-goog-api-key'] == 'test-secret'
        assert kwargs['json']['contents'][-1]['parts'][0]['text'] == 'How do I save water?'
        return httpx.Response(200, request=httpx.Request('POST', url), json={'candidates': [{'content': {'parts': [{'text': 'Fix leaking taps.'}]}}]})
    monkeypatch.setattr(assistant.network, 'request', request)
    r = client.post('/api/v1/assistant/chat', json={'message': 'How do I save water?'})
    assert r.status_code == 200
    assert r.json()['answer'] == 'Fix leaking taps.'


def test_missing_key(client, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', '')
    assert client.post('/api/v1/assistant/chat', json={'message': 'Hello'}).status_code == 503


def test_quota_error(client, monkeypatch):
    monkeypatch.setattr(assistant.network, 'request', lambda *a, **k: httpx.Response(429))
    assert client.post('/api/v1/assistant/chat', json={'message': 'Hello'}).status_code == 429


def test_empty_question_rejected(client):
    assert client.post('/api/v1/assistant/chat', json={'message': '  '}).status_code == 422


def test_requires_authentication():
    assert TestClient(app).post('/api/v1/assistant/chat', json={'message': 'Hello'}).status_code == 401

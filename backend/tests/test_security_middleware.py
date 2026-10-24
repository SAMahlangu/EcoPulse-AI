import asyncio

from backend.app.security_middleware import RequestBodyLimitMiddleware, SecurityHeadersMiddleware


def run_asgi(app, *, path, body=b"", headers=(), chunks=None):
    messages = []
    pending = list(chunks if chunks is not None else [body])

    async def receive():
        if pending:
            return {"type": "http.request", "body": pending.pop(0), "more_body": bool(pending)}
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": list(headers),
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
    }

    asyncio.run(app(scope, receive, send))
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_headers = dict(start["headers"])
    body_message = next(message for message in messages if message["type"] == "http.response.body")
    return start["status"], response_headers, body_message.get("body", b"")


async def read_body_app(scope, receive, send):
    while True:
        message = await receive()
        if message["type"] == "http.request" and not message.get("more_body", False):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def test_body_limit_rejects_large_declared_api_payload():
    payload = b"x" * (RequestBodyLimitMiddleware.DEFAULT_LIMIT + 1)
    status, _, _ = run_asgi(
        RequestBodyLimitMiddleware(read_body_app),
        path="/api/v1/public/contact",
        body=payload,
        headers=[(b"content-length", str(len(payload)).encode())],
    )
    assert status == 413


def test_body_limit_rejects_streamed_payload_without_content_length():
    status, _, _ = run_asgi(
        RequestBodyLimitMiddleware(read_body_app),
        path="/api/v1/public/contact",
        chunks=[b"x" * (1024 * 1024), b"y" * (1024 * 1024), b"z"],
    )
    assert status == 413


def test_completion_image_keeps_the_existing_ten_megabyte_upload_capacity():
    payload = b"x" * (3 * 1024 * 1024)
    status, _, _ = run_asgi(
        RequestBodyLimitMiddleware(read_body_app),
        path="/api/v1/infrastructure/reports/abc123/completion-image",
        body=payload,
        headers=[(b"content-length", str(len(payload)).encode())],
    )
    assert status == 200


def test_private_responses_get_safe_headers_and_are_not_cached(monkeypatch):
    monkeypatch.setenv("APP_PUBLIC_URL", "https://ecopulse.example")
    status, headers, _ = run_asgi(
        SecurityHeadersMiddleware(read_body_app),
        path="/api/v1/auth/me",
    )
    assert status == 200
    assert headers[b"x-content-type-options"] == b"nosniff"
    assert headers[b"x-frame-options"] == b"DENY"
    assert headers[b"permissions-policy"] == b"camera=(self), microphone=(), geolocation=(self)"
    assert headers[b"cache-control"] == b"no-store"
    assert headers[b"strict-transport-security"] == b"max-age=31536000"

"""Small, deployment-independent protections for the HTTP boundary."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from starlette.responses import JSONResponse


ASGIApp = Callable[..., Awaitable[None]]


class RequestBodyLimitMiddleware:
    """Bound request memory use before FastAPI parses JSON or uploaded forms."""

    DEFAULT_LIMIT = 2 * 1024 * 1024
    IMAGE_LIMIT = 11 * 1024 * 1024
    IMAGE_PATHS = {
        "/api/v1/incidents/analyze-image",
        "/api/v1/incidents/submit",
        "/api/v1/infrastructure/analyse",
        "/api/v1/infrastructure/detect",
        "/api/v1/infrastructure/reports/completion-image",
    }

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        is_image_upload = path in self.IMAGE_PATHS or (
            path.startswith("/api/v1/infrastructure/reports/")
            and path.endswith("/completion-image")
        )
        limit = self.IMAGE_LIMIT if is_image_upload else self.DEFAULT_LIMIT
        content_lengths = [value for name, value in scope.get("headers", []) if name.lower() == b"content-length"]
        if len(content_lengths) > 1:
            await JSONResponse({"detail": "Invalid request size."}, status_code=400)(scope, receive, send)
            return
        if content_lengths:
            try:
                declared_length = int(content_lengths[0])
            except ValueError:
                await JSONResponse({"detail": "Invalid request size."}, status_code=400)(scope, receive, send)
                return
            if declared_length < 0:
                await JSONResponse({"detail": "Invalid request size."}, status_code=400)(scope, receive, send)
                return
            if declared_length > limit:
                await self._too_large(scope, receive, send)
                return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                # Content-Length can be missing or dishonest, so also count streamed bytes.
                if received > limit:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            await self._too_large(scope, receive, send)

    async def _too_large(self, scope, receive, send) -> None:
        await JSONResponse(
            {"detail": "Request body is too large for this endpoint."},
            status_code=413,
        )(scope, receive, send)


class _RequestBodyTooLarge(Exception):
    pass


class SecurityHeadersMiddleware:
    """Add safe browser defaults and prevent private API responses being cached."""

    PRIVATE_API_PATHS = (
        "/api/v1/auth/",
        "/api/v1/resident/",
        "/api/v1/incidents",
        "/api/v1/notifications",
        "/api/v1/municipal/",
        "/api/v1/admin/",
        "/api/v1/infrastructure/",
        "/api/v1/water/",
        "/api/v1/energy/",
        "/api/v1/assistant/",
        "/api/v1/climate/preferences",
    )

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        public_url = os.getenv("APP_PUBLIC_URL", "").strip().lower()
        self.https_public_url = public_url.startswith("https://")

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                defaults = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"permissions-policy", b"camera=(self), microphone=(), geolocation=(self)"),
                ]
                if self.https_public_url:
                    defaults.append((b"strict-transport-security", b"max-age=31536000"))
                if path.startswith(self.PRIVATE_API_PATHS):
                    headers = [
                        (name, value)
                        for name, value in headers
                        if name.lower() not in {b"cache-control", b"pragma"}
                    ]
                    present = {name.lower() for name, _ in headers}
                    defaults.append((b"cache-control", b"no-store"))
                    defaults.append((b"pragma", b"no-cache"))
                headers.extend((name, value) for name, value in defaults if name not in present)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, secure_send)

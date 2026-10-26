"""Browser-only session cookies and CSRF protection; bearer API clients stay supported."""
from __future__ import annotations

import hmac
import os
import re
import secrets
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from jose import JWTError, jwt
from starlette.responses import Response

SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})
COOKIE_TTL = 7 * 24 * 60 * 60


def secure_cookies(request: Request) -> bool:
    public_url = os.getenv('APP_PUBLIC_URL', '').strip()
    secure = public_url.startswith('https://') or request.url.scheme == 'https'
    if not secure and request.url.hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise HTTPException(503, 'HTTPS is required for browser sessions.')
    return secure


def cookie_name(request: Request, kind: str) -> str:
    return ('__Host-' if secure_cookies(request) else '') + f'ecopulse-{kind}'


def session_cookie(request: Request, kind: str) -> str | None:
    return request.cookies.get(cookie_name(request, kind))


def trusted_origin(origin: str) -> bool:
    configured = {value.strip().rstrip('/') for value in os.getenv('CORS_ORIGINS', 'http://localhost:5173').split(',') if value.strip()}
    public_url = os.getenv('APP_PUBLIC_URL', '').strip().rstrip('/')
    if public_url:
        parsed_public = urlsplit(public_url)
        configured.add(f'{parsed_public.scheme}://{parsed_public.netloc}')
    parsed = urlsplit(origin)
    if parsed.username or parsed.password or parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
        return False
    if origin.rstrip('/') in configured:
        return True
    return bool({'http://localhost:5173', 'http://127.0.0.1:5173'} & configured and re.fullmatch(r'http://(?:localhost|127\.0\.0\.1|\[::1\]):(?:517[3-9]|4173)', origin))


def require_origin(request: Request, *, allow_missing: bool = False) -> None:
    origin = request.headers.get('origin', '')
    if not origin and allow_missing:
        return
    if not origin or not trusted_origin(origin):
        raise HTTPException(403, 'Use the configured EcoPulse website to manage your session.')


def require_csrf(request: Request) -> None:
    require_origin(request)
    expected = session_cookie(request, 'csrf')
    supplied = request.headers.get('x-csrf-token', '')
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(403, 'Your security token expired. Reload EcoPulse and try again.')


def request_access_token(request: Request, bearer: str | None = None) -> str | None:
    cookie_token = session_cookie(request, 'access')
    if cookie_token and request.method not in SAFE_METHODS:
        require_csrf(request)
    if bearer:
        return bearer
    return cookie_token


def _set_cookie(response: Response, request: Request, kind: str, value: str, max_age: int) -> None:
    response.set_cookie(cookie_name(request, kind), value, max_age=max_age, httponly=True,
                        secure=secure_cookies(request), samesite='lax', path='/')


def bootstrap_csrf(response: Response, request: Request) -> str:
    token = session_cookie(request, 'csrf') or secrets.token_urlsafe(32)
    _set_cookie(response, request, 'csrf', token, COOKIE_TTL)
    return token


def set_session(response: Response, request: Request, payload: dict) -> dict:
    access = payload.get('access_token')
    refresh = payload.get('refresh_token')
    if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
        raise HTTPException(502, 'The authentication provider returned an incomplete session.')
    try:
        ttl = max(1, min(int(payload.get('expires_in', 3600)), 24 * 60 * 60))
    except (ValueError, TypeError):
        raise HTTPException(502, 'The authentication provider returned an invalid session.') from None
    csrf = secrets.token_urlsafe(32)
    _set_cookie(response, request, 'access', access, ttl)
    _set_cookie(response, request, 'refresh', refresh, COOKIE_TTL)
    _set_cookie(response, request, 'csrf', csrf, COOKIE_TTL)
    return {'authenticated': True, 'csrf_token': csrf, 'expires_at': int(time.time()) + ttl}


def clear_session(response: Response, request: Request) -> None:
    for kind in ('access', 'refresh', 'csrf', 'pkce'):
        response.delete_cookie(cookie_name(request, kind), path='/', secure=secure_cookies(request), httponly=True, samesite='lax')


def session_expiry(request: Request) -> int:
    # Only a scheduling hint. Authentication always verifies the signed JWT separately.
    try:
        return int(jwt.get_unverified_claims(session_cookie(request, 'access') or '').get('exp', 0))
    except (JWTError, ValueError, TypeError):
        return 0


def set_pkce(response: Response, request: Request, verifier: str) -> None:
    _set_cookie(response, request, 'pkce', verifier, 15 * 60)

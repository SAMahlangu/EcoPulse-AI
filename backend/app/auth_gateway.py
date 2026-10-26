"""Supabase Auth gateway. Browser responses contain metadata, never session tokens."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from collections import OrderedDict, deque
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from starlette.concurrency import run_in_threadpool

from .browser_sessions import (bootstrap_csrf, clear_session, cookie_name, require_csrf, require_origin,
                               request_access_token, session_cookie, session_expiry, set_pkce, set_session, trusted_origin)
from .security import current_user
from .services import network
from .services.supabase_keys import supabase_publishable_key

router = APIRouter(prefix='/api/v1/auth')
_attempts: OrderedDict[str, deque] = OrderedDict()
_lock = threading.Lock()


class AuthBody(BaseModel):
    model_config = ConfigDict(extra='forbid')


class PasswordLogin(AuthBody):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=1024)


class PublicProfile(AuthBody):
    full_name: str = Field(min_length=2, max_length=120)


class Signup(PasswordLogin):
    data: PublicProfile


class Recover(AuthBody):
    email: str = Field(min_length=3, max_length=254)
    redirectTo: str | None = Field(default=None, max_length=2048)


class ChangePassword(AuthBody):
    password: SecretStr = Field(min_length=8, max_length=128)


class Exchange(AuthBody):
    code: SecretStr = Field(min_length=1, max_length=4096)


class LinkSession(AuthBody):
    access_token: SecretStr = Field(min_length=1, max_length=8192)
    refresh_token: SecretStr = Field(min_length=1, max_length=8192)


class VerifyFactor(AuthBody):
    factor_id: UUID
    code: SecretStr = Field(min_length=6, max_length=6)


def limit_attempts(request: Request, *, bucket: str = 'auth', limit: int = 30):
    actor = f'{bucket}:{request.client.host if request.client else "unknown"}'
    now = time.monotonic()
    with _lock:
        attempts = _attempts.setdefault(actor, deque())
        _attempts.move_to_end(actor)
        while attempts and attempts[0] < now - 60:
            attempts.popleft()
        if len(attempts) >= limit:
            raise HTTPException(429, 'Please wait a minute before trying again.', headers={'Retry-After': '60', 'Cache-Control': 'no-store'})
        attempts.append(now)
        while len(_attempts) > 1000:
            _attempts.popitem(last=False)


def _configuration() -> tuple[str, str]:
    base = os.getenv('SUPABASE_URL', '').strip().rstrip('/')
    key = supabase_publishable_key()
    if not base or not key or urlsplit(base).scheme != 'https':
        raise HTTPException(503, 'The sign-in service is not configured.')
    return base, key


async def _body(request: Request, model):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 16384:
            raise HTTPException(413, 'Authentication request is too large.')
    try:
        return model.model_validate(json.loads(data) if data else {})
    except (ValidationError, ValueError, TypeError):
        raise HTTPException(422, 'Check the authentication details and try again.') from None


async def _upstream(method: str, path: str, *, token: str | None = None, body=None, params=None) -> httpx.Response:
    base, key = _configuration()
    headers = {'apikey': key, 'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        return await run_in_threadpool(network.request, method, f'{base}/auth/v1/{path}',
                                       headers=headers, json=body, params=params, timeout=15)
    except httpx.HTTPError:
        raise HTTPException(503, 'The sign-in service is temporarily unreachable. Please retry.', headers={'Cache-Control': 'no-store'}) from None


def _data(result: httpx.Response) -> dict:
    if not 200 <= result.status_code < 300:
        # Provider payloads can contain session material or submitted fields.
        if result.status_code == 429:
            raise HTTPException(429, 'Please wait before trying again.', headers={'Retry-After': '60'})
        if result.status_code in {400, 401, 403, 422}:
            raise HTTPException(401, 'The authentication details or verification code are invalid or expired.')
        raise HTTPException(503, 'The authentication provider could not complete this request.')
    if result.status_code == 204:
        return {}
    try:
        value = result.json()
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except ValueError:
        raise HTTPException(502, 'The authentication provider returned an invalid response.') from None


def _response() -> JSONResponse:
    return JSONResponse({}, headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'})


def _json(response: JSONResponse, payload: dict) -> JSONResponse:
    response.body = response.render(payload)
    response.headers['content-length'] = str(len(response.body))
    return response


@router.get('/session')
def browser_session(request: Request):
    require_origin(request, allow_missing=True)
    response = _response()
    csrf = bootstrap_csrf(response, request)
    return _json(response, {'configured': bool(os.getenv('SUPABASE_URL', '').strip() and supabase_publishable_key()),
                            'authenticated': bool(session_cookie(request, 'access') or session_cookie(request, 'refresh')),
                            'csrf_token': csrf, 'expires_at': session_expiry(request)})


@router.api_route('/gateway/{action}', methods=['POST', 'PUT'])
async def auth_gateway(action: Literal['token', 'signup', 'recover', 'logout', 'user', 'exchange', 'exchange-link'], request: Request,
                       grant_type: Literal['password', 'refresh_token'] | None = Query(None)):
    require_csrf(request)
    limit_attempts(request, bucket='refresh' if action == 'token' and grant_type == 'refresh_token' else 'auth', limit=60 if grant_type == 'refresh_token' else 30)
    if request.method != ('PUT' if action == 'user' else 'POST'):
        raise HTTPException(405, 'Unsupported authentication method.')
    response = _response()
    if action == 'logout':
        await _body(request, AuthBody)
        token = session_cookie(request, 'access')
        provider_revoked = False
        try:
            if token:
                result = await _upstream('POST', 'logout', token=token, body={})
                provider_revoked = result.status_code in {200, 204, 401, 403}
        except HTTPException:
            pass
        clear_session(response, request)
        return _json(response, {'authenticated': False, 'provider_revoked': provider_revoked})
    if action == 'token':
        if grant_type == 'password':
            form = await _body(request, PasswordLogin)
            body = {'email': form.email, 'password': form.password.get_secret_value()}
        elif grant_type == 'refresh_token':
            await _body(request, AuthBody)
            refresh = session_cookie(request, 'refresh')
            if not refresh:
                raise HTTPException(401, 'Your session expired. Please sign in again.')
            body = {'refresh_token': refresh}
        else:
            raise HTTPException(422, 'Choose a supported sign-in method.')
        result = await _upstream('POST', 'token', body=body, params={'grant_type': grant_type})
        if grant_type == 'refresh_token' and result.status_code in {400, 401, 403, 422}:
            clear_session(response, request)
            response.status_code = 401
            return _json(response, {'detail': 'Your session expired. Please sign in again.'})
        return _json(response, set_session(response, request, _data(result)))
    if action == 'exchange':
        form = await _body(request, Exchange)
        verifier = session_cookie(request, 'pkce')
        if not verifier:
            raise HTTPException(401, 'Open this email link in the browser where you requested it, or request a new link.')
        data = _data(await _upstream('POST', 'token', body={'auth_code': form.code.get_secret_value(), 'code_verifier': verifier}, params={'grant_type': 'pkce'}))
        response.delete_cookie(cookie_name(request, 'pkce'), path='/')
        return _json(response, set_session(response, request, data))
    if action == 'exchange-link':
        # Transition for existing invitation/recovery links: consume once, no UI storage.
        form = await _body(request, LinkSession)
        token = form.access_token.get_secret_value()
        _data(await _upstream('GET', 'user', token=token))
        from jose import JWTError, jwt
        try:
            expires = int(jwt.get_unverified_claims(token)['exp']) - int(time.time())
            if expires <= 0:
                raise ValueError()
        except (JWTError, ValueError, KeyError, TypeError):
            raise HTTPException(401, 'The email link expired. Request a new link.') from None
        return _json(response, set_session(response, request, {'access_token': token, 'refresh_token': form.refresh_token.get_secret_value(), 'expires_in': expires}))
    if action in {'signup', 'recover'}:
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        if action == 'signup':
            form = await _body(request, Signup)
            body = {'email': form.email, 'password': form.password.get_secret_value(), 'data': form.data.model_dump()}
            redirect = os.getenv('APP_PUBLIC_URL', 'http://localhost:5173').rstrip('/') + '/login'
        else:
            form = await _body(request, Recover)
            body = {'email': form.email}
            redirect = form.redirectTo or os.getenv('APP_PUBLIC_URL', 'http://localhost:5173').rstrip('/') + '/reset-password'
        parsed = urlsplit(redirect)
        if not trusted_origin(f'{parsed.scheme}://{parsed.netloc}') or parsed.username or parsed.password:
            raise HTTPException(422, 'Use the configured website address for account recovery.')
        body.update(code_challenge=challenge, code_challenge_method='s256')
        data = _data(await _upstream('POST', action, body=body, params={'redirect_to': redirect}))
        set_pkce(response, request, verifier)
        if data.get('access_token'):
            return _json(response, set_session(response, request, data))
        return _json(response, {'authenticated': False, 'confirmation_required': action == 'signup'})
    form = await _body(request, ChangePassword)
    token = request_access_token(request)
    if not token:
        raise HTTPException(401, 'Use a fresh password reset link or sign in first.')
    _data(await _upstream('PUT', 'user', token=token, body={'password': form.password.get_secret_value()}))
    return _json(response, {'updated': True})


async def _factors(token: str) -> list[dict]:
    data = _data(await _upstream('GET', 'user', token=token))
    return [factor for factor in data.get('factors', []) if isinstance(factor, dict) and factor.get('factor_type') == 'totp']


@router.get('/mfa/factors')
async def mfa_factors(request: Request, user: dict = Depends(current_user)):
    token = request_access_token(request)
    if not token:
        raise HTTPException(401, 'A browser session is required.')
    return {'items': [{key: factor.get(key) for key in ('id', 'friendly_name', 'factor_type', 'status')} for factor in await _factors(token)], 'aal': user.get('aal', 'aal1')}


@router.post('/mfa/enroll')
async def mfa_enroll(request: Request, user: dict = Depends(current_user)):
    require_csrf(request)
    limit_attempts(request, bucket=f'mfa:{user["id"]}', limit=10)
    await _body(request, AuthBody)
    token = request_access_token(request)
    if not token:
        raise HTTPException(401, 'A browser session is required.')
    for factor in await _factors(token):
        if factor.get('status') == 'unverified':
            try:
                factor_id = str(UUID(factor['id']))
            except (ValueError, KeyError, TypeError):
                continue
            _data(await _upstream('DELETE', f'factors/{factor_id}', token=token))
    data = _data(await _upstream('POST', 'factors', token=token, body={'factor_type': 'totp', 'friendly_name': f'EcoPulse {secrets.token_hex(3)}', 'issuer': 'EcoPulse'}))
    totp = data.get('totp', {})
    if not isinstance(totp, dict) or not totp.get('secret') or not data.get('id'):
        raise HTTPException(502, 'Authenticator setup is unavailable. Please retry.')
    return {'id': data['id'], 'totp': {key: totp.get(key) for key in ('qr_code', 'secret', 'uri')}}


@router.post('/mfa/verify')
async def mfa_verify(request: Request, user: dict = Depends(current_user)):
    require_csrf(request)
    limit_attempts(request, bucket=f'mfa:{user["id"]}', limit=10)
    form = await _body(request, VerifyFactor)
    code = form.code.get_secret_value()
    if not code.isascii() or not code.isdigit():
        raise HTTPException(422, 'Enter the six digits shown in your authenticator.')
    token = request_access_token(request)
    if not token:
        raise HTTPException(401, 'A browser session is required.')
    challenge = _data(await _upstream('POST', f'factors/{form.factor_id}/challenge', token=token, body={}))
    if not challenge.get('id'):
        raise HTTPException(502, 'The authenticator challenge could not be created.')
    data = _data(await _upstream('POST', f'factors/{form.factor_id}/verify', token=token, body={'challenge_id': challenge['id'], 'code': code}))
    response = _response()
    return _json(response, set_session(response, request, data))

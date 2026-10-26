from __future__ import annotations

import os
import socket
import logging
import time
from typing import Any

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwk, jwt
from .services import network
from .browser_sessions import request_access_token

log = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)
_jwks: dict[str, Any] = {"expires": 0, "keys": []}


def database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def local_demo_enabled() -> bool:
    return os.getenv("ECOPULSE_LOCAL_DEMO", "false").strip().lower() == "true"


def postgres_connection():
    if not database_url():
        raise HTTPException(status_code=503, detail="Supabase database access is not configured.")
    try:
        import psycopg
        from psycopg.rows import dict_row
        connection_options = {'row_factory': dict_row, 'connect_timeout': 10}
        if network.fallback_enabled():
            # Preserve the configured hostname, credentials and TLS settings.
            # libpq's hostaddr bypasses name lookup only, not authentication.
            from psycopg.conninfo import conninfo_to_dict
            info = conninfo_to_dict(database_url())
            host = info.get('host', '')
            if host and ',' not in host and not info.get('hostaddr'):
                try:
                    socket.getaddrinfo(host, info.get('port', '5432'))
                except socket.gaierror:
                    connection_options['hostaddr'] = network.resolve_ipv4(host)[0]
        return psycopg.connect(database_url(), **connection_options)
    except Exception as exc:
        log.exception("Supabase Postgres connection failed")
        raise HTTPException(status_code=503, detail="Supabase database is currently unavailable.") from exc


def current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict[str, Any]:
    # Resolve the actor from the verified session subject on every request; never trust an owner ID from a request body.
    token = request_access_token(request, credentials.credentials if credentials and credentials.scheme.lower() == 'bearer' else None)
    if not token:
        raise HTTPException(status_code=401, detail="Sign in is required.", headers={"WWW-Authenticate": "Bearer"})
    secret = os.getenv("SUPABASE_JWT_SECRET", "").strip()
    supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    if not supabase_url:
        raise HTTPException(status_code=503, detail="Supabase authentication verification is not configured.")
    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        if algorithm == "HS256" and secret:
            verification_key: Any = secret
        elif algorithm in ("RS256", "ES256"):
            now = time.monotonic()
            key_id = header.get("kid")
            jwk_value = next((key for key in _jwks["keys"] if key.get("kid") == key_id), None)
            if now >= _jwks["expires"] or jwk_value is None:
                try:
                    response = network.request('GET', f"{supabase_url}/auth/v1/.well-known/jwks.json", timeout=8)
                    response.raise_for_status()
                    keys = response.json().get("keys", [])
                    if not isinstance(keys, list):
                        raise ValueError("Supabase returned an invalid signing-key set")
                    _jwks.update(keys=keys, expires=now + 300)
                    jwk_value = next((key for key in keys if key.get("kid") == key_id), None)
                except (httpx.HTTPError, ValueError):
                    # Keep authenticating with a known key during a brief Supabase/network outage.
                    if jwk_value is None:
                        raise
            if not jwk_value:
                raise JWTError("Unknown signing key")
            verification_key = jwk.construct(jwk_value, algorithm=algorithm)
        else:
            raise JWTError("Unsupported signing algorithm")
        claims = jwt.decode(token, verification_key, algorithms=[algorithm], audience="authenticated", issuer=f"{supabase_url}/auth/v1")
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="The session is invalid or expired.", headers={"WWW-Authenticate": "Bearer"}) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Could not retrieve the Supabase signing keys.") from exc
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="The session is invalid.")
    try:
        with postgres_connection() as connection:
            row = connection.execute("""
                select p.id::text, p.full_name, p.role, p.municipality_id::text,
                       p.province,p.suburb,p.community_id::text,p.onboarding_completed_at,p.notification_preferences,
                       coalesce(p.account_status, 'active') as account_status
                from public.profiles p where p.id = %s
            """, (user_id,)).fetchone()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Unable to load the authorised user profile.") from exc
    if not row:
        raise HTTPException(status_code=403, detail="No authorised EcoPulse profile exists for this account.")
    profile_id = row["id"]
    full_name = row["full_name"]
    role = row["role"]
    municipality_id = row["municipality_id"]
    account_status = row["account_status"]
    if account_status != "active":
        raise HTTPException(status_code=403, detail="This account is suspended.")
    if role not in ("resident", "municipal", "admin"):
        raise HTTPException(status_code=403, detail="This account has no authorised platform role.")
    municipalities: list[str] = []
    if role == "municipal":
        try:
            with postgres_connection() as connection:
                memberships = connection.execute("""
                    select municipality_id::text from public.municipal_memberships
                    where user_id = %s and status = 'active' order by municipality_id
                """, (user_id,)).fetchall()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Unable to verify municipal membership.") from exc
        municipalities = [item["municipality_id"] for item in memberships]
        if not municipalities:
            raise HTTPException(status_code=403, detail="No active municipal membership is assigned to this account.")
    assurance = claims.get('aal', 'aal1')
    mfa_required = role in {'municipal', 'admin'} and assurance != 'aal2'
    # Even routes depending directly on current_user (notifications, for example)
    # must not expose privileged data until the signed session has passed MFA.
    mfa_exempt_paths = {
        '/api/v1/auth/session', '/api/v1/auth/me', '/api/v1/auth/mfa/factors',
        '/api/v1/auth/mfa/enroll', '/api/v1/auth/mfa/verify',
    }
    is_auth_gateway = request.url.path.startswith('/api/v1/auth/gateway/')
    if mfa_required and request.url.path not in mfa_exempt_paths and not is_auth_gateway:
        raise HTTPException(403, detail={'code': 'mfa_required', 'message': 'Verify your authenticator code to open this workspace.'})
    return {"id": profile_id, "email": claims.get("email", ""), "full_name": full_name, "role": role,
            "municipality_id": municipality_id, "municipality_ids": municipalities,
            "province": row["province"], "suburb": row["suburb"], "community_id": row["community_id"],
            "onboarding_completed_at": row["onboarding_completed_at"], "notification_preferences": row["notification_preferences"],
            'aal': assurance, 'mfa_required': mfa_required, 'session_expires_at': claims.get('exp')}


def optional_current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict[str, Any] | None:
    if credentials is None and request_access_token(request) is None:
        return None
    return current_user(request, credentials)


def require_roles(*roles: str):
    def dependency(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="You are not authorised to access this resource.")
        if user.get('mfa_required'):
            raise HTTPException(403, detail={'code': 'mfa_required', 'message': 'Verify your authenticator code to open this workspace.'})
        return user
    return dependency

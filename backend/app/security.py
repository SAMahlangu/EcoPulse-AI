from __future__ import annotations

import os
import time
from typing import Any

import httpx
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwk, jwt

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
        return psycopg.connect(database_url(), row_factory=dict_row)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Supabase database is currently unavailable.") from exc


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict[str, Any]:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Sign in is required.", headers={"WWW-Authenticate": "Bearer"})
    secret = os.getenv("SUPABASE_JWT_SECRET", "").strip()
    supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    if not supabase_url:
        raise HTTPException(status_code=503, detail="Supabase authentication verification is not configured.")
    try:
        header = jwt.get_unverified_header(credentials.credentials)
        algorithm = header.get("alg")
        if algorithm == "HS256" and secret:
            verification_key: Any = secret
        elif algorithm in ("RS256", "ES256"):
            now = time.monotonic()
            if now >= _jwks["expires"]:
                response = httpx.get(f"{supabase_url}/auth/v1/.well-known/jwks.json", timeout=5)
                response.raise_for_status()
                _jwks.update(keys=response.json().get("keys", []), expires=now + 300)
            jwk_value = next((key for key in _jwks["keys"] if key.get("kid") == header.get("kid")), None)
            if not jwk_value:
                raise JWTError("Unknown signing key")
            verification_key = jwk.construct(jwk_value, algorithm=algorithm)
        else:
            raise JWTError("Unsupported signing algorithm")
        claims = jwt.decode(credentials.credentials, verification_key, algorithms=[algorithm], audience="authenticated", issuer=f"{supabase_url}/auth/v1")
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="The session is invalid or expired.", headers={"WWW-Authenticate": "Bearer"}) from exc
    except httpx.HTTPError as exc:
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
    return {"id": profile_id, "email": claims.get("email", ""), "full_name": full_name, "role": role,
            "municipality_id": municipality_id, "municipality_ids": municipalities,
            "province": row["province"], "suburb": row["suburb"], "community_id": row["community_id"],
            "onboarding_completed_at": row["onboarding_completed_at"], "notification_preferences": row["notification_preferences"]}


def optional_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict[str, Any] | None:
    if credentials is None:
        return None
    return current_user(credentials)


def require_roles(*roles: str):
    def dependency(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="You are not authorised to access this resource.")
        return user
    return dependency

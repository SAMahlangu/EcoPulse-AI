from __future__ import annotations

import asyncio
import os
import re
import hashlib
import json
import logging
import secrets
import time
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, SecretStr
from starlette.concurrency import run_in_threadpool

from .services import municipality_boundaries, network, weather_service
from .services.supabase_keys import supabase_secret_headers, supabase_secret_key
from .security import current_user, postgres_connection, require_roles
from .email_notifications import send_user_notification_email

router = APIRouter(prefix="/api/v1")
log = logging.getLogger(__name__)
_contact_attempts: dict[str, deque[float]] = defaultdict(deque)


class ContactRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: str = Field(pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$", max_length=254)
    organisation: str | None = Field(default=None, max_length=160)
    subject: str = Field(min_length=3, max_length=160)
    message: str = Field(min_length=10, max_length=4000)
    website: str = Field(default="", max_length=200)


class StatusUpdate(BaseModel):
    status: Literal["under_review", "verified", "in_progress", "resolved", "duplicate", "rejected", "unable_to_verify"]
    message: str = Field(min_length=3, max_length=2000)
    resolution_note: str | None = Field(default=None, max_length=2000)


class IncidentNote(BaseModel):
    message: str = Field(min_length=3, max_length=3000)
    internal: bool = True


class AccountStatusRequest(BaseModel):
    status: Literal["active", "suspended"]


class RoleRequest(BaseModel):
    role: Literal["resident", "municipal", "admin"]


class MunicipalityRequest(BaseModel):
    municipality_id: UUID


class CommunityCreateRequest(BaseModel):
    municipality_id: UUID
    name: str = Field(min_length=2, max_length=120, pattern=r"\S.*\S")


class MunicipalityImportVerification(BaseModel):
    confirmed_active: bool
    verification_note: str = Field(min_length=10, max_length=2000)


class MunicipalInvitationRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=120, pattern=r"\S.*\S")
    email: str = Field(pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$", max_length=254)
    province_id: UUID
    municipality_id: UUID


class MunicipalOfficialRequest(MunicipalInvitationRequest):
    password: SecretStr = Field(min_length=8, max_length=128, exclude=True)


class LocalResidentRegistration(BaseModel):
    model_config = {"extra": "forbid"}
    full_name: str = Field(min_length=2, max_length=120, pattern=r"\S.*\S")
    email: str = Field(pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$", max_length=254)
    password: SecretStr = Field(min_length=8, max_length=128, exclude=True)


class AcceptInvitationRequest(BaseModel):
    token: str = Field(min_length=32, max_length=200)


class MembershipStatusRequest(BaseModel):
    status: Literal["active", "revoked"]


class NoticeRequest(BaseModel):
    municipality_id: UUID
    category: Literal["water", "electricity", "road", "maintenance", "emergency"]
    title: str = Field(min_length=3, max_length=120)
    body: str = Field(min_length=10, max_length=4000)
    status: Literal["draft", "published"] = "draft"
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    demonstration: bool = True


class NoticeUpdate(BaseModel):
    category: Literal["water", "electricity", "road", "maintenance", "emergency"] | None = None
    title: str | None = Field(default=None, min_length=3, max_length=120)
    body: str | None = Field(default=None, min_length=10, max_length=4000)
    status: Literal["draft", "published", "archived"] | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class ResidentOnboarding(BaseModel):
    province_id: UUID
    municipality_id: UUID
    community_id: UUID | None = None
    suburb: str = Field(min_length=2, max_length=120)
    notification_preferences: dict[str, bool] = Field(default_factory=dict)


def _execute(sql: str, params: tuple[Any, ...] = (), *, fetch: str = "all"):
    try:
        with postgres_connection() as connection:
            cursor = connection.execute(sql, params)
            return cursor.fetchone() if fetch == "one" else cursor.fetchall()
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Workspace database query failed")
        raise HTTPException(status_code=503, detail="The shared platform database is unavailable or needs migration 003.") from exc



def _municipal_execute(user, sql, params=(), *, fetch="all"):
    # The SQL function also verifies active municipality membership and account status.
    # UUID validation prevents SQL interpolation of untrusted identifiers.
    actor = str(UUID(user["id"]))
    sql = sql.replace("public.incidents", "public.authorized_incidents('" + actor + "'::uuid)")
    return _execute(sql, params, fetch=fetch)

def _scope(user: dict[str, Any]) -> list[str]:
    return user.get("municipality_ids") or []


@router.get("/locations/provinces")
def location_provinces():
    rows = _execute("select id::text,name,code from public.provinces order by name")
    return {"items": [dict(row) for row in rows]}


def _municipality_key(name: str) -> str:
    """Compare administrative labels without fuzzy or substring matching."""
    name = re.sub(r"\bward\s*\d+\b.*$", "", name.casefold()).strip()
    name = re.sub(r"^city of\s+", "", name)
    name = re.sub(r"\s+(?:(?:metropolitan|local|district)\s+)?municipality$", "", name)
    return " ".join(re.findall(r"\w+", name))


@router.get("/locations/reverse")
async def reverse_geocode_location(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
):
    """Resolve a selected point to a nearby label and canonical SA geography."""
    place: dict[str, Any] = {}
    try:
        # Nearby street/suburb names are helpful, but should not hold up the
        # official municipality fallback when the label provider is slow.
        place = await asyncio.wait_for(
            weather_service.reverse_location(latitude, longitude), timeout=6,
        )
    except Exception as exc:
        # A location label provider can be unavailable while database boundaries
        # still identify the responsible municipality and province.
        log.warning("Reverse geocoding provider unavailable (%s)", type(exc).__name__)

    municipality = None
    province = None
    official_province = None
    country_code = (place.get("country_code") or "").casefold() or None
    if country_code in (None, "za"):
        try:
            matches = _execute(
                """select m.id::text,m.name,m.province_id::text,p.name as province
                   from public.municipalities m join public.provinces p on p.id=m.province_id
                   where m.is_demonstration is false and m.boundary is not null
                     and m.municipality_type in ('local','metropolitan')
                     and extensions.st_covers(m.boundary::extensions.geometry,
                         extensions.st_setsrid(extensions.st_makepoint(%s,%s),4326))""",
                (longitude, latitude),
            )
            if len(matches) == 1:
                municipality = dict(matches[0])
                place["boundary_source"] = "Municipality boundary data"
        except HTTPException:
            pass

        # Some municipalities have no imported boundary. Use an exact official
        # municipality name returned by reverse geocoding when available.
        if municipality is None:
            candidates = list(dict.fromkeys(filter(None, (
                place.get("municipality"), place.get("locality"),
                *place.get("municipality_candidates", []),
            ))))
            if candidates:
                try:
                    matches = _execute(
                        """select m.id::text,m.name,m.province_id::text,p.name as province
                           from public.municipalities m join public.provinces p on p.id=m.province_id
                           where m.is_demonstration is false
                             and m.municipality_type in ('local','metropolitan')
                             and lower(m.name)=any(%s)
                             and lower(p.name)=lower(coalesce(%s,p.name))""",
                        ([name.casefold() for name in candidates], place.get("province")),
                    )
                    if len(matches) == 1:
                        municipality = dict(matches[0])
                except HTTPException:
                    pass

            if municipality is None and candidates and place.get("province"):
                try:
                    rows = _execute(
                        """select m.id::text,m.name,m.province_id::text,p.name as province
                           from public.municipalities m join public.provinces p on p.id=m.province_id
                           where m.is_demonstration is false
                             and m.municipality_type in ('local','metropolitan')
                             and lower(p.name)=lower(%s)""", (place["province"],))
                    keys = {_municipality_key(name) for name in candidates} - {""}
                    matches = [row for row in rows if _municipality_key(row["name"]) in keys]
                    if len(matches) == 1:
                        municipality = dict(matches[0])
                except HTTPException:
                    pass

        # Official MDB polygons cover municipalities even when imported
        # PostGIS boundaries are missing and the place-name provider is down.
        if municipality is None:
            try:
                official_area = await municipality_boundaries.municipality_at_point(
                    latitude, longitude,
                )
            except Exception as exc:
                log.warning("Official municipal boundary lookup unavailable (%s)", type(exc).__name__)
                official_area = None
            if official_area:
                official_province = official_area["province"]
                country_code = "za"
                place["boundary_source"] = official_area["source"]
                try:
                    rows = _execute(
                        """select m.id::text,m.name,m.province_id::text,p.name as province
                           from public.municipalities m join public.provinces p on p.id=m.province_id
                           where m.is_demonstration is false
                             and m.municipality_type in ('local','metropolitan')
                             and lower(p.name)=lower(%s)""",
                        (official_area["province"],),
                    )
                    key = _municipality_key(official_area["name"])
                    matches = [row for row in rows if _municipality_key(row["name"]) == key]
                    if len(matches) == 1:
                        municipality = dict(matches[0])
                except HTTPException:
                    pass

    if municipality:
        province = {"id": municipality["province_id"], "name": municipality["province"]}
    elif country_code in (None, "za") and (place.get("province") or official_province):
        try:
            row = _execute(
                "select id::text,name from public.provinces where lower(name)=lower(%s)",
                (official_province or place.get("province"),), fetch="one",
            )
            if row:
                province = dict(row)
        except HTTPException:
            pass

    return {
        "location": place.get("label") or (municipality["name"] if municipality else ""),
        "locality": place.get("locality"),
        "province": province,
        "municipality": municipality,
        "source": place.get("source") if place.get("label") else place.get("boundary_source"),
        "boundary_source": place.get("boundary_source"),
        "country_code": country_code,
    }


@router.get("/locations/municipalities")
def location_municipalities(
    province: str | None = Query(None, min_length=1, max_length=100),
    province_id: UUID | None = Query(None),
    search: str | None = Query(None, min_length=1, max_length=100),
    municipality_type: Literal["metropolitan", "district", "local"] | None = Query(None),
    onboarded_only: bool = Query(False),
    eligible_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    source = "from public.municipalities m join public.provinces p on p.id=m.province_id"
    clauses = ["m.is_demonstration is false"]
    params: list[Any] = []
    if province_id:
        clauses.append("p.id = %s")
        params.append(province_id)
    if province:
        clauses.append("lower(p.name) = lower(%s)")
        params.append(province.strip())
    if municipality_type:
        clauses.append("m.municipality_type = %s")
        params.append(municipality_type)
    if search:
        clauses.append("(m.name ilike %s or m.code ilike %s)")
        params.extend((f"%{search}%", f"%{search}%"))
    where = " and ".join(clauses)
    selection = f"""select m.id::text,m.name,m.code,p.name as province,p.id::text as province_id,
        m.municipality_type,m.parent_municipality_id::text,
        case m.municipality_type when 'metropolitan' then 'A' when 'district' then 'C' when 'local' then 'B' end as category,
        coalesce((to_jsonb(m)->>'is_onboarded')::boolean,false) as is_onboarded
        {source} where {where}"""
    total = _execute(
        f"select count(*) as total from ({selection}) options",
        tuple(params), fetch="one",
    )["total"]
    rows = _execute(
        f"{selection} order by name,code limit %s offset %s",
        tuple(params + [limit, offset]),
    )
    return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset,
            "reference_notice": "Municipality references from the database; inclusion does not imply current activity or government partnership."}


@router.get("/locations/municipalities/{municipality_id}")
def location_municipality(municipality_id: UUID):
    row = _execute(
        """select m.id::text,m.province_id::text,m.province,m.name,m.code,
                  m.municipality_type,m.parent_municipality_id::text,
                  parent.name as parent_municipality,coalesce((to_jsonb(m)->>'is_onboarded')::boolean,false) as is_onboarded
           from public.municipalities m
           left join public.municipalities parent on parent.id=m.parent_municipality_id
           where m.id=%s
             and m.is_demonstration is false""",
        (municipality_id,), fetch="one",
    )
    if not row:
        raise HTTPException(status_code=404, detail="Municipality reference not found.")
    return dict(row)


@router.get("/locations/communities")
def location_communities(
    municipality_id: UUID = Query(...),
    search: str | None = Query(None, min_length=1, max_length=100),
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    clauses = ["c.municipality_id=%s", """exists(select 1 from public.municipalities m where m.id=c.municipality_id
        and m.is_demonstration is false)"""]
    params: list[Any] = [municipality_id]
    if search:
        clauses.append("c.name ilike %s")
        params.append(f"%{search}%")
    where = " and ".join(clauses)
    total = _execute(
        f"select count(*) as total from public.communities c where {where}",
        tuple(params), fetch="one",
    )["total"]
    rows = _execute(
        f"select c.id::text,c.municipality_id::text,c.name from public.communities c where {where} order by c.name limit %s offset %s",
        tuple(params + [limit, offset]),
    )
    return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def _signed_media_url(storage_path: str) -> str | None:
    supabase_url=os.getenv("SUPABASE_URL","").strip().rstrip("/")
    service_key=supabase_secret_key()
    if not supabase_url or not service_key: return None
    try:
        response=network.request('POST', f"{supabase_url}/storage/v1/object/sign/incident-media/{storage_path}",headers=supabase_secret_headers(),json={"expiresIn":300},timeout=8)
        response.raise_for_status()
        value=response.json().get("signedURL")
        return f"{supabase_url}/storage/v1{value}" if value and value.startswith("/") else value
    except (httpx.HTTPError,ValueError):
        return None


@router.post("/auth/register-local", status_code=201)
async def register_local_resident(form: LocalResidentRegistration, request: Request):
    # Explicit opt-in and a loopback connection keep this out of deployed public registration.
    if os.getenv("ECOPULSE_LOCAL_PASSWORD_SIGNUP", "false").lower() != "true":
        raise HTTPException(status_code=404, detail="Local registration is disabled.")
    try:
        public_host = urlsplit(os.getenv("APP_PUBLIC_URL", "http://localhost:5173")).hostname
    except ValueError:
        public_host = None
    if public_host not in {"localhost", "127.0.0.1", "::1"}:
        # A loopback reverse proxy can make remote requests appear to come from localhost.
        raise HTTPException(status_code=404, detail="Local registration is disabled.")
    if request.url.hostname not in {"localhost", "127.0.0.1", "::1", "[::1]"}:
        raise HTTPException(status_code=404, detail="Local registration is disabled.")
    if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="This registration mode is available on localhost only.")
    supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    service_key = supabase_secret_key()
    if not supabase_url or not service_key:
        raise HTTPException(status_code=503, detail="Local registration requires server-side Supabase configuration.")
    headers = supabase_secret_headers()
    async with httpx.AsyncClient(timeout=15, transport=network.AsyncDNSFallbackTransport()) as client:
        try:
            response = await client.post(f"{supabase_url}/auth/v1/admin/users", headers=headers, json={
                "email": form.email.strip().lower(), "password": form.password.get_secret_value(),
                "email_confirm": True, "user_metadata": {"full_name": form.full_name.strip()},
            })
        except httpx.HTTPError:
            raise HTTPException(status_code=503, detail="Registration could not be completed. Try signing in before registering again.") from None
        if response.status_code >= 400:
            try:
                error = response.json()
                code = error.get("error_code") or error.get("code", "")
            except (ValueError, AttributeError):
                code = ""
            if code in {"email_exists", "user_already_exists"}:
                raise HTTPException(status_code=409, detail="This email is already registered. Sign in with your existing password.")
            raise HTTPException(status_code=400, detail="Could not register. Check the email and password requirements.")
        try:
            resident_id = UUID(response.json()["id"])
        except (ValueError, KeyError, TypeError):
            raise HTTPException(status_code=503, detail="Unexpected registration result. Try signing in before registering again.") from None
        try:
            profile = _execute("select role from public.profiles where id=%s", (resident_id,), fetch="one")
            if not profile or profile["role"] != "resident":
                raise RuntimeError("Resident profile was not provisioned")
        except Exception:
            try:
                cleanup = await client.delete(f"{supabase_url}/auth/v1/admin/users/{resident_id}", headers=headers)
                cleanup.raise_for_status()
            except Exception:
                raise HTTPException(status_code=503, detail="Registration setup could not finish. Ask an administrator to check the account before retrying.") from None
            raise HTTPException(status_code=503, detail="Resident profile setup failed. Please try registering again.") from None
    return {"id": str(resident_id), "requires_email_confirmation": False}


@router.get("/auth/me")
def auth_me(user: dict[str, Any] = Depends(current_user)):
    return {key: user.get(key) for key in ("id", "email", "full_name", "role", "municipality_id", "municipality_ids", "province", "suburb", "community_id", "onboarding_completed_at", "notification_preferences")}


@router.get("/resident/onboarding")
async def resident_onboarding(user: dict[str, Any] = Depends(require_roles("resident"))):
    profile = await run_in_threadpool(_execute, """select coalesce(m.province_id,p.id)::text as province_id,coalesce(m.province,profile.province) as province,
        profile.municipality_id::text,m.name as municipality_name,profile.community_id::text,profile.suburb,
        coalesce(
            case when community.centroid is not null then extensions.st_y(community.centroid::extensions.geometry) end,
            case when m.boundary is not null then extensions.st_y(extensions.st_centroid(m.boundary::extensions.geometry)) end
        ) as latitude,
        coalesce(
            case when community.centroid is not null then extensions.st_x(community.centroid::extensions.geometry) end,
            case when m.boundary is not null then extensions.st_x(extensions.st_centroid(m.boundary::extensions.geometry)) end
        ) as longitude,
        profile.notification_preferences,profile.onboarding_completed_at
        from public.profiles profile left join public.municipalities m on m.id=profile.municipality_id
        left join public.communities community on community.id=profile.community_id and community.municipality_id=m.id
        left join public.provinces p on lower(p.name)=lower(profile.province)
        where profile.id=%s""", (user["id"],), fetch="one")
    if profile and profile.get("latitude") is None and profile.get("longitude") is None and profile.get("municipality_name") and profile.get("province"):
        try:
            coordinates = await weather_service.municipality_coordinates(profile["municipality_name"], profile["province"])
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Municipality coordinate lookup failed: %s", type(exc).__name__)
        else:
            if coordinates:
                profile = {**profile, **coordinates}
    return {"profile": dict(profile) if profile else None}


@router.put("/resident/onboarding")
def save_resident_onboarding(form: ResidentOnboarding, user: dict[str, Any] = Depends(require_roles("resident"))):
    municipality = _execute("select province,province_id from public.municipalities m where id=%s and province_id=%s and m.is_demonstration is false", (form.municipality_id, form.province_id), fetch="one")
    if not municipality:
        raise HTTPException(status_code=422, detail="Select a municipality that belongs to the chosen province.")
    if form.community_id:
        community = _execute("select 1 from public.communities where id=%s and municipality_id=%s", (form.community_id, form.municipality_id), fetch="one")
        if not community:
            raise HTTPException(status_code=422, detail="The selected community does not belong to that municipality.")
    allowed = {"incident_updates", "climate_alerts", "municipal_notices"}
    preferences = {key: value for key, value in form.notification_preferences.items() if key in allowed}
    row = _execute("""with saved as (
        update public.profiles set province=%s,municipality_id=%s,community_id=%s,suburb=%s,
        notification_preferences=%s::jsonb,onboarding_completed_at=now(),updated_at=now()
        where id=%s returning province,municipality_id,community_id,suburb,notification_preferences,onboarding_completed_at
        ) select saved.province,m.province_id::text as province_id,saved.municipality_id::text,
        saved.community_id::text,saved.suburb,saved.notification_preferences,saved.onboarding_completed_at
        from saved join public.municipalities m on m.id=saved.municipality_id""",
        (municipality["province"], form.municipality_id, form.community_id, form.suburb.strip(), json.dumps(preferences), user["id"]), fetch="one")
    if not row:
        raise HTTPException(status_code=404, detail="Resident profile not found.")
    return dict(row)


@router.post("/auth/accept-municipal-invitation")
def accept_municipal_invitation(form: AcceptInvitationRequest, user: dict[str, Any] = Depends(current_user)):
    token_hash = hashlib.sha256(form.token.encode("utf-8")).hexdigest()
    try:
        with postgres_connection() as connection:
            invitation = connection.execute("""select id::text,municipality_id::text from public.municipal_invitations
                where lower(email)=lower(%s) and token_hash=%s and status='pending' and expires_at>now() for update""",
                (user["email"],token_hash)).fetchone()
            if not invitation:
                raise HTTPException(status_code=400, detail="This invitation is invalid, expired, or belongs to another account.")
            profile = connection.execute("""update public.profiles p
                set role='municipal',municipality_id=m.id,province_id=m.province_id,province=m.province,updated_at=now()
                from public.municipalities m
                where m.id=%s and m.is_onboarded and p.id=%s
                returning p.id""", (invitation["municipality_id"],user["id"])).fetchone()
            if not profile:
                raise HTTPException(status_code=400, detail="This invitation municipality is no longer onboarded.")
            connection.execute("insert into public.municipal_memberships(user_id,municipality_id,status) values(%s,%s,'active') on conflict(user_id,municipality_id) do update set status='active',updated_at=now()",(user["id"],invitation["municipality_id"]))
            connection.execute("""insert into public.department_memberships(user_id,department_id,role,is_active)
                select %s,id,'employee',true from public.municipal_departments
                where municipality_id=%s and is_triage and is_active
                on conflict(user_id,department_id) do update set is_active=true""",
                (user["id"],invitation["municipality_id"]))
            connection.execute("update public.municipal_invitations set status='accepted' where id=%s",(invitation["id"],))
            connection.execute("insert into public.admin_audit_logs(actor_id,action,target_type,target_id,details) values(%s,'municipal_invitation_accepted','municipal_invitation',%s,'{}'::jsonb)",(user["id"],invitation["id"]))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not complete the municipal invitation.") from exc
    return {"role":"municipal","municipality_id":invitation["municipality_id"]}


@router.post("/public/contact", status_code=202)
def submit_contact(form: ContactRequest, request: Request):
    if form.website.strip():
        return {"accepted": True}
    host = request.client.host if request.client else "unknown"
    now = time.monotonic()
    attempts = _contact_attempts[host]
    while attempts and now - attempts[0] > 3600:
        attempts.popleft()
    if len(attempts) >= 5:
        raise HTTPException(status_code=429, detail="Please wait before sending another message.")
    attempts.append(now)
    # A configured service database is required; contact content is not persisted to a local demo DB.
    _execute("insert into public.contact_enquiries(full_name,email,organisation,subject,message) values (%s,%s,%s,%s,%s) returning id",
             (form.full_name.strip(), form.email.strip().lower(), form.organisation, form.subject.strip(), form.message.strip()), fetch="one")
    return {"accepted": True}


@router.get("/admin/enquiries")
def admin_enquiries(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
                    user: dict[str, Any] = Depends(require_roles("admin"))):
    rows = _execute("""select id::text,full_name,email,organisation,subject,message,created_at
        from public.contact_enquiries order by created_at desc,id desc limit %s offset %s""", (limit, offset))
    count = _execute("select count(*) as total from public.contact_enquiries", fetch="one")
    return {"items": [dict(row) for row in rows], "total": count["total"], "limit": limit, "offset": offset}


@router.get("/notifications")
def notifications(limit: int = Query(25,ge=1,le=100), offset: int = Query(0,ge=0), user: dict[str, Any] = Depends(current_user)):
    rows=_execute("select id::text,title,body,category,official,read_at,created_at from public.notifications where resident_id=%s order by created_at desc limit %s offset %s",(user["id"],limit,offset))
    unread=_execute("select count(*) as count from public.notifications where resident_id=%s and read_at is null",(user["id"],),fetch="one")
    return {"items":[dict(row) for row in rows],"unread_count":unread["count"],"limit":limit,"offset":offset}


@router.patch("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: str, user: dict[str, Any] = Depends(current_user)):
    row=_execute("update public.notifications set read_at=coalesce(read_at,now()) where id=%s and resident_id=%s returning id::text,read_at",(notification_id,user["id"]),fetch="one")
    if not row: raise HTTPException(status_code=404,detail="Notification not found.")
    return dict(row)


@router.get("/public/notices")
def public_notices(limit: int = Query(25,ge=1,le=100)):
    rows=_execute("select n.id::text,n.category,n.title,n.body,n.demonstration,n.starts_at,n.ends_at,n.created_at,m.name as municipality,m.province from public.municipal_notices n join public.municipalities m on m.id=n.municipality_id where n.status='published' and (n.starts_at is null or n.starts_at<=now()) and (n.ends_at is null or n.ends_at>now()) order by n.created_at desc limit %s",(limit,))
    return {"items":[dict(row) for row in rows]}


@router.get("/municipal/dashboard")
def municipal_dashboard(department_id: UUID | None = None, user: dict[str, Any] = Depends(require_roles("municipal"))):
    rows = _municipal_execute(user, """
      select count(*) as total,
        count(*) filter(where status='submitted') as new,
        count(*) filter(where status in ('under_review','acknowledged')) as under_review,
        count(*) filter(where status='in_progress') as in_progress,
        count(*) filter(where status in ('resolved','closed')) as resolved,
        count(*) filter(where priority in ('high','urgent')) as high_priority
      from public.incidents where municipality_id = any(%s::uuid[])
      and (%s::uuid is null or department_id=%s)
    """, (_scope(user),department_id,department_id), fetch="one")
    recent = _municipal_execute(user, """
      select i.id::text, i.category, i.title, i.status, i.priority, i.created_at,
        i.location::text as location, i.reporter_id::text
      from public.incidents i where i.municipality_id = any(%s::uuid[])
      and (%s::uuid is null or i.department_id=%s)
      order by i.created_at desc limit 8
    """, (_scope(user),department_id,department_id))
    return {"metrics": dict(rows), "recent_incidents": [dict(row) for row in recent], "municipality_ids": _scope(user)}


@router.get("/municipal/incidents")
def municipal_incidents(q: str | None = Query(None, max_length=120), category: str | None = None,
                        status: str | None = None, priority: str | None = None,
                        start_date: date | None = None, end_date: date | None = None,
                        limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
                        user: dict[str, Any] = Depends(require_roles("municipal"))):
    rows = _municipal_execute(user, """
      select i.id::text, i.public_reference, i.category, i.title, i.description, i.location::text as location,
        i.status, i.priority, i.created_at, i.updated_at, i.resolution_note,
        i.reporter_id::text, i.municipality_id::text,i.department_id::text,(select department_name from public.municipal_departments d where d.id=i.department_id) as department_name
      from public.incidents i
      where i.municipality_id = any(%s::uuid[])
        and (%s::text is null or i.category=%s)
        and (%s::text is null or i.status=%s)
        and (%s::text is null or i.priority=%s)
        and (%s::date is null or i.created_at::date >= %s)
        and (%s::date is null or i.created_at::date <= %s)
        and (%s::text is null or i.title ilike %s or i.description ilike %s or i.location::text ilike %s)
      order by i.created_at desc limit %s offset %s
    """, (_scope(user), category, category, status, status, priority, priority, start_date, start_date, end_date, end_date,
          q, f"%{q}%" if q else None, f"%{q}%" if q else None, f"%{q}%" if q else None, limit, offset))
    return {"items": [dict(row) for row in rows], "limit": limit, "offset": offset}


@router.get("/municipal/incidents/{incident_id}")
def municipal_incident(incident_id: str, user: dict[str, Any] = Depends(require_roles("municipal"))):
    row = _municipal_execute(user, """
      select i.id::text, i.public_reference, i.category, i.title, i.description, i.location::text as location,
        i.status, i.priority, i.created_at, i.updated_at, i.resolution_note,
        i.reporter_id::text, i.municipality_id::text,i.department_id::text,(select department_name from public.municipal_departments d where d.id=i.department_id) as department_name,i.ai_suggested_category,i.resident_confirmed_category,i.ai_model_name,i.ai_model_version
      from public.incidents i where i.id=%s and i.municipality_id = any(%s::uuid[])
    """, (incident_id, _scope(user)), fetch="one")
    if not row:
        raise HTTPException(status_code=404, detail="Incident not found.")
    history = _municipal_execute(user, "select id::text,status,message,official,internal,created_at from public.incident_updates where incident_id=%s order by created_at", (incident_id,))
    media = _municipal_execute(user, "select storage_path,content_type from public.incident_media where incident_id=%s order by created_at",(incident_id,))
    media_items=[{"content_type":item["content_type"],"url":_signed_media_url(item["storage_path"])} for item in media]
    return {"incident": dict(row), "history": [dict(item) for item in history], "media":media_items}


@router.patch("/municipal/incidents/{incident_id}/status")
def update_incident_status(incident_id: str, update: StatusUpdate,
                           background_tasks: BackgroundTasks,
                           user: dict[str, Any] = Depends(require_roles("municipal"))):
    try:
        with postgres_connection() as connection:
            allowed = connection.execute("select public.can_manage_incident(%s,%s) as allowed", (incident_id,user["id"])).fetchone()
            if not allowed or not allowed["allowed"]:
                raise HTTPException(404, "Incident not found in your authorised department.")
            current = connection.execute("select status,reporter_id::text,title from public.incidents where id=%s and municipality_id=any(%s::uuid[]) for update",(incident_id,_scope(user))).fetchone()
            if not current:
                raise HTTPException(status_code=404, detail="Incident not found in your authorised municipality.")
            transitions = {
                "submitted": {"under_review","verified","in_progress","duplicate","rejected","unable_to_verify"},
                "acknowledged": {"under_review","verified","in_progress","duplicate","rejected","unable_to_verify"},
                "under_review": {"verified","in_progress","duplicate","rejected","unable_to_verify"},
                "verified": {"in_progress","resolved","duplicate","rejected","unable_to_verify"},
                "assigned": {"in_progress","rejected","unable_to_verify"},
                "in_progress": {"resolved","unable_to_verify"},
                "resolved": set(), "closed": set(), "duplicate": set(), "rejected": set(), "unable_to_verify": set(),
            }
            if update.status not in transitions.get(current["status"], set()):
                raise HTTPException(status_code=409, detail=f"Status cannot move from {current['status']} to {update.status}.")
            row = connection.execute("""
              update public.incidents set status=%s, resolution_note=coalesce(%s,resolution_note), updated_at=now(),
                municipal_verified_category=case when %s='verified' then category else municipal_verified_category end,
                municipal_verified_by=case when %s='verified' then %s else municipal_verified_by end,
                municipal_verified_at=case when %s='verified' then now() else municipal_verified_at end
              where id=%s and municipality_id=any(%s::uuid[])
              returning id::text, reporter_id::text, title, status
            """, (update.status, update.resolution_note, update.status, update.status, user["id"], update.status, incident_id, _scope(user))).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Incident not found in your authorised municipality.")
            connection.execute("insert into public.incident_updates(incident_id,author_id,status,message,official,internal) values(%s,%s,%s,%s,true,false)",
                               (incident_id, user["id"], update.status, update.message))
            connection.execute("insert into public.notifications(resident_id,title,body,category,official) values(%s,%s,%s,'incident_update',true)",
                               (row["reporter_id"], f"Report update: {row['title']}", update.message))
            connection.execute("insert into public.admin_audit_logs(actor_id,action,target_type,target_id,details) values(%s,'incident_status_changed','incident',%s,%s::jsonb)",
                               (user["id"], incident_id, '{"status":"' + update.status + '"}'))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not update the incident. Confirm migration 003 is applied.") from exc
    background_tasks.add_task(send_user_notification_email, row["reporter_id"], f"Report update: {row['title']}", update.message, "incident_updates")
    return {"id": row["id"], "status": row["status"], "updated_at": datetime.now(timezone.utc).isoformat()}


@router.post("/municipal/incidents/{incident_id}/notes", status_code=201)
def add_incident_note(incident_id: str, note: IncidentNote,
                      background_tasks: BackgroundTasks,
                      user: dict[str, Any] = Depends(require_roles("municipal"))):
    try:
        with postgres_connection() as connection:
            allowed = connection.execute("select public.can_manage_incident(%s,%s) as allowed", (incident_id,user["id"])).fetchone()
            if not allowed or not allowed["allowed"]:
                raise HTTPException(404, "Incident not found in your authorised department.")
            exists = connection.execute("select id from public.incidents where id=%s and municipality_id=any(%s::uuid[])", (incident_id, _scope(user))).fetchone()
            if not exists:
                raise HTTPException(status_code=404, detail="Incident not found in your authorised municipality.")
            reporter = connection.execute("select reporter_id::text,title from public.incidents where id=%s",(incident_id,)).fetchone()
            row = connection.execute("insert into public.incident_updates(incident_id,author_id,message,official,internal) values(%s,%s,%s,true,%s) returning id::text,created_at", (incident_id,user["id"],note.message,note.internal)).fetchone()
            if not note.internal:
                connection.execute("insert into public.notifications(resident_id,title,body,category,official) values(%s,%s,%s,'incident_update',true)",(reporter["reporter_id"],f"Report update: {reporter['title']}",note.message))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not save the incident note.") from exc
    if not note.internal:
        background_tasks.add_task(send_user_notification_email, reporter["reporter_id"], f"Report update: {reporter['title']}", note.message, "incident_updates")
    return {"id": row["id"], "created_at": row["created_at"]}


@router.get("/municipal/analytics")
def municipal_analytics(user: dict[str, Any] = Depends(require_roles("municipal"))):
    status = _municipal_execute(user, "select status,count(*) as count from public.incidents where municipality_id=any(%s::uuid[]) group by status order by status", (_scope(user),))
    categories = _municipal_execute(user, "select category,count(*) as count from public.incidents where municipality_id=any(%s::uuid[]) group by category order by count desc", (_scope(user),))
    trends = _municipal_execute(user, "select date_trunc('month',created_at)::date as month,count(*) as count from public.incidents where municipality_id=any(%s::uuid[]) group by 1 order by 1 desc limit 12", (_scope(user),))
    timing = _municipal_execute(user, "select count(*) filter(where status in ('resolved','closed')) as resolved_count, avg(extract(epoch from (updated_at-created_at))/3600) filter(where status in ('resolved','closed')) as mean_resolution_hours from public.incidents where municipality_id=any(%s::uuid[])", (_scope(user),), fetch="one")
    return {"status_distribution":[dict(x) for x in status],"categories":[dict(x) for x in categories],"monthly_trends":[dict(x) for x in reversed(trends)],"resolution":dict(timing)}


@router.get("/municipal/map")
def municipal_map(user: dict[str, Any] = Depends(require_roles("municipal")), limit: int = Query(1000,ge=1,le=1000),
                  offset: int = Query(0,ge=0),
                  department_id: UUID | None=None, municipality_id: UUID | None=None,
                  category: str | None=None,status: str | None=None,priority: str | None=None,
                  start_date: date | None=None,end_date: date | None=None):
    clauses=['municipality_id=any(%s::uuid[])','location is not null',
             'extensions.st_x(location::extensions.geometry) is not null',
             'extensions.st_y(location::extensions.geometry) is not null']
    params=[_scope(user)]
    for column,value in [('department_id',department_id),('municipality_id',municipality_id),('category',category),('status',status),('priority',priority)]:
        if value is not None:
            clauses.append(f'{column}=%s');params.append(value)
    if start_date: clauses.append('created_at::date>=%s');params.append(start_date)
    if end_date: clauses.append('created_at::date<=%s');params.append(end_date)
    where=' and '.join(clauses)
    total = _municipal_execute(user, 'select count(*) as total from public.incidents where '+where, tuple(params), fetch='one')['total']
    rows = _municipal_execute(user, 'select id::text,public_reference,category,title,status,priority,location_text,created_at,extensions.st_y(location::extensions.geometry) as latitude,extensions.st_x(location::extensions.geometry) as longitude from public.incidents where '+where+' order by created_at desc limit %s offset %s', tuple(params+[limit,offset]))
    return {"items":[dict(row) for row in rows],"total":total,"limit":limit,"offset":offset}


@router.get("/municipal/notices")
def municipal_notices(user: dict[str, Any] = Depends(require_roles("municipal"))):
    rows = _municipal_execute(user, "select id::text,municipality_id::text,category,title,body,status,starts_at,ends_at,demonstration,created_at from public.municipal_notices where municipality_id=any(%s::uuid[]) order by created_at desc limit 100", (_scope(user),))
    return {"items":[dict(row) for row in rows]}


@router.get("/municipal/my-municipalities")
def my_municipalities(user: dict[str, Any] = Depends(require_roles("municipal"))):
    ids = _scope(user)
    rows = _municipal_execute(user,
        """select id::text,name,province,province_id::text
           from public.municipalities where id=any(%s::uuid[]) order by province,name""",
        (ids,),
    )
    return {"items": [dict(row) for row in rows]}


@router.post("/municipal/notices", status_code=201)
def create_notice(form: NoticeRequest, user: dict[str, Any] = Depends(require_roles("municipal"))):
    if str(form.municipality_id) not in _scope(user):
        raise HTTPException(status_code=403, detail="You cannot publish for this municipality.")
    if form.ends_at and form.starts_at and form.ends_at <= form.starts_at:
        raise HTTPException(status_code=422, detail="Notice end time must follow its start time.")
    row = _municipal_execute(user, "insert into public.municipal_notices(municipality_id,author_id,category,title,body,status,starts_at,ends_at,demonstration) values(%s,%s,%s,%s,%s,%s,%s,%s,true) returning id::text,status,created_at", (form.municipality_id,user["id"],form.category,form.title.strip(),form.body.strip(),form.status,form.starts_at,form.ends_at), fetch="one")
    _municipal_execute(user, "insert into public.admin_audit_logs(actor_id,action,target_type,target_id,details) values(%s,'municipal_notice_created','municipal_notice',%s,%s::jsonb)", (user["id"],row["id"],'{"status":"'+form.status+'"}'))
    return {"id":row["id"],"status":row["status"],"created_at":row["created_at"],"demonstration":True}


@router.patch("/municipal/notices/{notice_id}")
def update_notice(notice_id: str, form: NoticeUpdate, user: dict[str, Any] = Depends(require_roles("municipal"))):
    changes=form.model_dump(exclude_unset=True)
    if not changes: raise HTTPException(status_code=422,detail="Supply at least one notice field to update.")
    fields={"category","title","body","status","starts_at","ends_at"}
    if set(changes)-fields: raise HTTPException(status_code=422,detail="Unsupported notice field.")
    if changes.get("ends_at") and changes.get("starts_at") and changes["ends_at"]<=changes["starts_at"]:
        raise HTTPException(status_code=422,detail="Notice end time must follow its start time.")
    set_clause=", ".join(f"{key}=%s" for key in changes)
    values=list(changes.values())
    try:
        with postgres_connection() as connection:
            row=connection.execute(f"update public.municipal_notices set {set_clause},updated_at=now() where id=%s and municipality_id=any(%s::uuid[]) returning id::text,status,title,demonstration",(*values,notice_id,_scope(user))).fetchone()
            if not row: raise HTTPException(status_code=404,detail="Notice not found in your authorised municipality.")
            connection.execute("insert into public.admin_audit_logs(actor_id,action,target_type,target_id,details) values(%s,'municipal_notice_updated','municipal_notice',%s,%s::jsonb)",(user["id"],notice_id,'{"status":"'+row["status"]+'"}'))
    except HTTPException: raise
    except Exception as exc: raise HTTPException(status_code=503,detail="Could not update this notice.") from exc
    return dict(row)


@router.get("/municipal/reports")
def municipal_reports(user: dict[str, Any] = Depends(require_roles("municipal")), limit: int = Query(1000,ge=1,le=5000)):
    rows = _municipal_execute(user, "select id::text,category,title,status,priority,created_at,updated_at,municipality_id::text from public.incidents where municipality_id=any(%s::uuid[]) order by created_at desc limit %s", (_scope(user),limit))
    return {"items":[dict(row) for row in rows],"limit":limit}


@router.get("/admin/dashboard")
def admin_dashboard(user: dict[str, Any] = Depends(require_roles("admin"))):
    row = _execute("select (select count(*) from public.profiles) as users,(select count(*) from public.profiles where account_status='active') as active_users,(select count(*) from public.municipalities) as municipalities,(select count(*) from public.incidents) as incidents,(select count(*) from public.climate_advisories where valid_until is null or valid_until>now()) as active_advisories", fetch="one")
    return {"metrics":dict(row),"api":{"status":"available"},"ai_models":[{"name":"Climate risk screening","version":os.getenv("CLIMATE_ENGINE_VERSION","rule-based"),"kind":"rule-based","status":"available","metrics":None}]}


@router.get("/admin/users")
def admin_users(user: dict[str, Any] = Depends(require_roles("admin")), q: str | None = Query(None,max_length=120), role: str | None = None, limit: int = Query(50,ge=1,le=100), offset: int = Query(0,ge=0)):
    rows = _execute("select p.id::text,p.full_name,p.role,p.account_status,p.created_at,u.email from public.profiles p join auth.users u on u.id=p.id where (%s::text is null or p.role=%s) and (%s::text is null or p.full_name ilike %s or u.email ilike %s) order by p.created_at desc limit %s offset %s", (role,role,q,f"%{q}%" if q else None,f"%{q}%" if q else None,limit,offset))
    return {"items":[dict(row) for row in rows],"limit":limit,"offset":offset}


@router.patch("/admin/users/{user_id}/status")
def set_account_status(user_id: str, form: AccountStatusRequest, user: dict[str, Any] = Depends(require_roles("admin"))):
    status = form.status
    if user_id == user["id"] and status == "suspended":
        raise HTTPException(status_code=422, detail="You cannot suspend your own account.")
    try:
        with postgres_connection() as connection:
            row = connection.execute("update public.profiles set account_status=%s,updated_at=now() where id=%s returning id::text,account_status",(status,user_id)).fetchone()
            if not row:
                raise HTTPException(status_code=404,detail="User not found.")
            connection.execute("insert into public.admin_audit_logs(actor_id,action,target_type,target_id,details) values(%s,'account_status_changed','user',%s,%s::jsonb)",(user["id"],user_id,'{"status":"'+status+'"}'))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503,detail="Could not update this account.") from exc
    return {"id":row["id"],"status":row["account_status"]}


@router.patch("/admin/users/{user_id}/role")
def set_user_role(user_id: str, form: RoleRequest, user: dict[str, Any] = Depends(require_roles("admin"))):
    role = form.role
    if user_id==user["id"] and role!="admin":
        raise HTTPException(status_code=422,detail="You cannot remove your own administrator access here.")
    try:
        with postgres_connection() as connection:
            if role=="municipal":
                active=connection.execute("select exists(select 1 from public.municipal_memberships where user_id=%s and status='active') as ok",(user_id,)).fetchone()["ok"]
                if not active: raise HTTPException(status_code=422,detail="Assign an active municipal membership before changing this role.")
            row=connection.execute("update public.profiles set role=%s,updated_at=now() where id=%s returning id::text,role",(role,user_id)).fetchone()
            if not row: raise HTTPException(status_code=404,detail="User not found.")
            connection.execute("insert into public.admin_audit_logs(actor_id,action,target_type,target_id,details) values(%s,'user_role_changed','user',%s,%s::jsonb)",(user["id"],user_id,'{"role":"'+role+'"}'))
    except HTTPException: raise
    except Exception as exc: raise HTTPException(status_code=503,detail="Could not update the user role.") from exc
    return {"id":row["id"],"role":row["role"]}


@router.get("/admin/municipalities")
def admin_municipalities(user: dict[str, Any] = Depends(require_roles("admin"))):
    rows = _execute("select m.id::text,m.name,m.province,m.province_id::text,m.code,m.municipality_type,m.parent_municipality_id::text,m.is_demonstration,m.reference_verified,m.reference_active,coalesce((to_jsonb(m)->>'is_onboarded')::boolean,false) as is_onboarded,m.created_at,(select count(*) from public.municipal_memberships mm where mm.municipality_id=m.id and mm.status='active') as active_members from public.municipalities m where m.is_demonstration is false order by m.province,m.name")
    return {"items":[dict(row) for row in rows]}


@router.get("/admin/communities")
def admin_communities(municipality_id: UUID = Query(...), user: dict[str, Any] = Depends(require_roles("admin"))):
    rows = _execute("""select c.id::text,c.municipality_id::text,c.name,c.created_at,
      (select count(*) from public.profiles p where p.community_id=c.id) as resident_count
      from public.communities c join public.municipalities m on m.id=c.municipality_id
      where c.municipality_id=%s and m.is_demonstration is false order by c.name""", (municipality_id,))
    return {"items": [dict(row) for row in rows]}


@router.post("/admin/communities", status_code=201)
def create_community(form: CommunityCreateRequest, user: dict[str, Any] = Depends(require_roles("admin"))):
    name = form.name.strip()
    try:
        with postgres_connection() as connection:
            municipality = connection.execute("""select id::text,name from public.municipalities
              where id=%s and is_demonstration is false and municipality_type in ('local','metropolitan')""",
              (form.municipality_id,)).fetchone()
            if not municipality:
                raise HTTPException(status_code=422, detail="Choose a verified, active municipality from the official list.")
            row = connection.execute("""insert into public.communities(municipality_id,name)
              values(%s,%s) on conflict(municipality_id,name) do nothing
              returning id::text,municipality_id::text,name,created_at""", (form.municipality_id,name)).fetchone()
            if not row:
                raise HTTPException(status_code=409, detail="That community is already configured for this municipality.")
            connection.execute("""insert into public.admin_audit_logs(actor_id,action,target_type,target_id,details)
              values(%s,'community_created','community',%s,%s::jsonb)""",
              (user["id"],row["id"],json.dumps({"municipality_id":row["municipality_id"],"name":name})))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not save the community. Confirm the database migrations are applied.") from exc
    return dict(row)


@router.get("/admin/municipality-imports")
def admin_municipality_imports(
    province_id: UUID | None = Query(None),
    search: str | None = Query(None, min_length=1, max_length=100),
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: dict[str, Any] = Depends(require_roles("admin")),
):
    clauses = ["true"]
    params: list[Any] = []
    if province_id:
        clauses.append("r.province_id = %s")
        params.append(province_id)
    if search:
        clauses.append("(r.name ilike %s or r.code ilike %s)")
        params.extend((f"%{search}%", f"%{search}%"))
    where = " and ".join(clauses)
    total = _execute(
        f"select count(*) as total from public.municipality_import_review r where {where}",
        tuple(params), fetch="one",
    )["total"]
    rows = _execute(
        f"""select r.code,r.name,r.long_name,r.source_parent_code,
                   r.source_province_code,r.source_province_name,r.province_id::text,
                   r.category,r.miif_category,r.source_is_active,
                   r.verification_status,r.confirmed_active,r.verification_note,
                   r.promoted_municipality_id::text,r.promoted_at,
                   existing.id::text as existing_municipality_id,
                   existing.name as existing_municipality_name
            from public.municipality_import_review r
            left join public.municipalities existing
              on upper(trim(existing.code)) = upper(trim(r.code))
            where {where}
            order by r.source_province_name,r.name limit %s offset %s""",
        tuple(params + [limit, offset]),
    )
    return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.put("/admin/municipality-imports/{code}/verification")
def verify_municipality_import(
    code: str,
    form: MunicipalityImportVerification,
    user: dict[str, Any] = Depends(require_roles("admin")),
):
    normalized_code = code.strip().upper()
    if not normalized_code or len(normalized_code) > 20:
        raise HTTPException(status_code=422, detail="Invalid municipality code.")
    note = form.verification_note.strip()
    if len(note) < 10:
        raise HTTPException(status_code=422, detail="Add a verification note with its source and check date.")
    try:
        with postgres_connection() as connection:
            row = connection.execute(
                """update public.municipality_import_review
                   set verification_status='verified', confirmed_active=%s,
                       verification_note=%s, verified_by=%s, verified_at=now(), updated_at=now()
                   where code=%s and province_id is not null
                   returning code,name,verification_status,confirmed_active,verified_at""",
                (form.confirmed_active, note, user["id"], normalized_code),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Import row not found or province could not be matched.")
            connection.execute(
                """insert into public.admin_audit_logs(actor_id,action,target_type,target_id,details)
                   values(%s,'municipality_import_verified','municipality_import',%s,%s::jsonb)""",
                (user["id"], normalized_code, json.dumps({"confirmed_active": form.confirmed_active, "verification_note": note})),
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not save the municipality verification decision.") from exc
    return dict(row)


@router.post("/admin/municipalities")
def create_municipality(form: MunicipalityRequest, user: dict[str, Any] = Depends(require_roles("admin"))):
    try:
        with postgres_connection() as connection:
            row = connection.execute("select id::text,name,province,province_id::text,code,municipality_type,parent_municipality_id::text,is_demonstration,is_onboarded,created_at from public.municipalities where id=%s and municipality_type is not null and reference_verified is true and reference_active is true for update", (form.municipality_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Select an official municipality reference.")
            if not row["is_onboarded"]:
                connection.execute("update public.municipalities set is_onboarded=true where id=%s", (form.municipality_id,))
                connection.execute("insert into public.admin_audit_logs(actor_id,action,target_type,target_id,details) values(%s,'municipality_onboarded','municipality',%s,%s::jsonb)", (user["id"], row["id"], json.dumps({"code":row["code"],"type":row["municipality_type"]})))
                row["is_onboarded"] = True
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503,detail="Could not onboard this municipality reference.") from exc
    return dict(row)


@router.post("/admin/municipal-officials", status_code=201)
async def add_municipal_official(form: MunicipalOfficialRequest, user: dict[str, Any] = Depends(require_roles("admin"))):
    supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    service_key = supabase_secret_key()
    if not supabase_url or not service_key:
        raise HTTPException(status_code=503, detail="Official account creation requires server-side Supabase configuration.")
    email = form.email.strip().lower()
    # Validate before creating an Auth user. Recheck under a lock when granting access.
    location_sql = """select m.id,m.name,p.id as province_id,p.name as province
        from public.municipalities m join public.provinces p on p.id=m.province_id
        where m.id=%s and p.id=%s and m.is_demonstration is false"""
    municipality = _execute(location_sql, (form.municipality_id, form.province_id), fetch="one")
    if not municipality:
        raise HTTPException(status_code=422, detail="Select a municipality belonging to the selected province.")
    if _execute("select id from auth.users where lower(email)=lower(%s)", (email,), fetch="one"):
        raise HTTPException(status_code=409, detail="An account with this email already exists. Existing accounts are not overwritten.")
    headers = supabase_secret_headers()
    async with httpx.AsyncClient(timeout=15, transport=network.AsyncDNSFallbackTransport()) as client:
        try:
            response = await client.post(f"{supabase_url}/auth/v1/admin/users", headers=headers, json={
                "email": email, "password": form.password.get_secret_value(), "email_confirm": True,
                "user_metadata": {"full_name": form.full_name.strip()},
            })
        except httpx.HTTPError:
            raise HTTPException(status_code=503, detail="The account service could not be reached. Check the user list before retrying.") from None
        if response.status_code >= 400:
            try:
                failure = response.json()
                code = failure.get("error_code") or failure.get("code", "")
            except (ValueError, AttributeError):
                code = ""
            if code in ("email_exists", "user_already_exists"):
                raise HTTPException(status_code=409, detail="An account with this email already exists.")
            raise HTTPException(status_code=400, detail="Could not create the account. Check the email and Supabase password requirements.")
        try:
            official_id = UUID(response.json()["id"])
        except (ValueError, KeyError, TypeError):
            raise HTTPException(status_code=503, detail="The account service returned an unexpected result. Check the user list before retrying.") from None
        try:
            with postgres_connection() as connection:
                municipality = connection.execute(location_sql + " for share of m", (form.municipality_id, form.province_id)).fetchone()
                if not municipality:
                    raise HTTPException(status_code=422, detail="The selected municipality is no longer available in this province.")
                # Role is assigned only here by an authenticated administrator, never from editable metadata.
                connection.execute("""insert into public.profiles
                    (id,full_name,role,municipality_id,province,account_status)
                    values(%s,%s,'municipal',%s,%s,'active')
                    on conflict(id) do update set full_name=excluded.full_name,role='municipal',
                    municipality_id=excluded.municipality_id,
                    province=excluded.province,account_status='active',updated_at=now()""",
                    (official_id, form.full_name.strip(), form.municipality_id, municipality["province"]))
                connection.execute("""insert into public.municipal_memberships(user_id,municipality_id,status)
                    values(%s,%s,'active') on conflict(user_id,municipality_id)
                    do update set status='active',updated_at=now()""", (official_id, form.municipality_id))
                connection.execute("""insert into public.department_memberships(user_id,department_id,role,is_active)
                    select %s,id,'employee',true from public.municipal_departments
                    where municipality_id=%s and is_triage and is_active
                    on conflict(user_id,department_id) do update set is_active=true""",
                    (official_id, form.municipality_id))
                connection.execute("""insert into public.admin_audit_logs(actor_id,action,target_type,target_id,details)
                    values(%s,'municipal_official_created','profile',%s,%s::jsonb)""",
                    (user["id"], str(official_id), json.dumps({"municipality_id": str(form.municipality_id), "province_id": str(form.province_id)})))
        except Exception as exc:
            # Auth and PostgreSQL cannot share a transaction. Remove only the newly created Auth user
            # if profile/membership provisioning fails; existing accounts are never modified.
            try:
                cleanup = await client.delete(f"{supabase_url}/auth/v1/admin/users/{official_id}", headers=headers)
                if cleanup.status_code >= 400:
                    raise RuntimeError("Auth cleanup failed")
            except Exception:
                raise HTTPException(status_code=503, detail="Account setup failed and automatic cleanup could not finish. Check the user list before retrying.") from None
            if isinstance(exc, HTTPException):
                raise exc
            raise HTTPException(status_code=503, detail="Official access could not be saved. The newly created account was removed; please retry.") from None
    return {"id": str(official_id), "email": email, "full_name": form.full_name.strip(),
            "municipality_id": str(form.municipality_id), "province_id": str(form.province_id),
            "municipality": municipality["name"], "province": municipality["province"], "role": "municipal", "status": "active"}


@router.get("/admin/municipal-memberships")
def admin_memberships(user: dict[str, Any] = Depends(require_roles("admin"))):
    rows=_execute("select mm.id::text,mm.user_id::text,u.email,p.full_name,p.role,mm.municipality_id::text,m.name as municipality,m.province,mm.status,mm.created_at from public.municipal_memberships mm join public.profiles p on p.id=mm.user_id join auth.users u on u.id=mm.user_id join public.municipalities m on m.id=mm.municipality_id order by mm.created_at desc limit 200")
    return {"items":[dict(row) for row in rows]}


@router.patch("/admin/municipal-memberships/{membership_id}")
def set_membership_status(membership_id: str, form: MembershipStatusRequest, user: dict[str, Any] = Depends(require_roles("admin"))):
    try:
        with postgres_connection() as connection:
            row=connection.execute("update public.municipal_memberships set status=%s,updated_at=now() where id=%s returning id::text,user_id::text,municipality_id::text,status",(form.status,membership_id)).fetchone()
            if not row: raise HTTPException(status_code=404,detail="Membership not found.")
            if form.status=="revoked":
                connection.execute("update public.profiles set role='resident',municipality_id=null,updated_at=now() where id=%s and not exists(select 1 from public.municipal_memberships where user_id=%s and status='active')",(row["user_id"],row["user_id"]))
            connection.execute("insert into public.admin_audit_logs(actor_id,action,target_type,target_id,details) values(%s,'municipal_membership_status_changed','municipal_membership',%s,%s::jsonb)",(user["id"],membership_id,'{"status":"'+form.status+'"}'))
    except HTTPException: raise
    except Exception as exc: raise HTTPException(status_code=503,detail="Could not update the membership.") from exc
    return {"id":row["id"],"status":row["status"]}


@router.get("/admin/data")
def admin_data(user: dict[str, Any] = Depends(require_roles("admin"))):
    return {"datasets":[{"name":"Climate observations","table":"weather_observations","source":"Configured weather providers","status":"schema available"},{"name":"Climate risk assessments","table":"climate_risk_assessments","source":"EcoPulse rule-based climate screening","status":"schema available"},{"name":"Community resources","table":"community_resources","source":"Curated records","status":"schema available"}],"generated_at":datetime.now(timezone.utc).isoformat()}


@router.get("/admin/ai/models")
def admin_models(user: dict[str, Any] = Depends(require_roles("admin"))):
    models = _execute("""select model_name,model_version,count(*) as analyses,
      count(*) filter(where available) as available_analyses,
      avg(confidence) filter(where available) as mean_reported_confidence,
      max(created_at) as last_analysis from public.incident_ai_analyses group by model_name,model_version order by last_analysis desc""")
    configured = bool(os.getenv("ECOPULSE_INCIDENT_MODEL", "").strip())
    incident = [{"name":row["model_name"],"kind":"configured incident vision adapter","version":row["model_version"],"status":"available" if row["available_analyses"] else "unavailable","last_evaluation":row["last_analysis"],"metrics":{"analyses":row["analyses"],"available_analyses":row["available_analyses"],"mean_reported_confidence":float(row["mean_reported_confidence"]) if row["mean_reported_confidence"] is not None else None,"accuracy":None}} for row in models]
    if not incident:
        incident=[{"name":"Incident classification","kind":"optional configured vision adapter","version":None,"status":"configured but no analyses recorded" if configured else "unavailable","last_evaluation":None,"metrics":None}]
    return {"items":[{"name":"Climate risk assessment","kind":"transparent rule-based engine","version":os.getenv("CLIMATE_ENGINE_VERSION","configured in backend"),"status":"available","last_evaluation":None,"metrics":None},*incident,{"name":"Water forecasting","kind":"not implemented","version":None,"status":"unavailable","last_evaluation":None,"metrics":None},{"name":"Energy forecasting","kind":"not implemented","version":None,"status":"unavailable","last_evaluation":None,"metrics":None},{"name":"Recommendation engine","kind":"not implemented","version":None,"status":"unavailable","last_evaluation":None,"metrics":None}]}


@router.get("/admin/ai/analyses")
def admin_ai_analyses(user: dict[str, Any] = Depends(require_roles("admin")), limit: int = Query(25,ge=1,le=100), offset: int = Query(0,ge=0)):
    rows=_execute("select id::text,model_name,model_version,available,suggested_category,confidence,created_at from public.incident_ai_analyses order by created_at desc limit %s offset %s",(limit,offset))
    return {"items":[dict(row) for row in rows],"limit":limit,"offset":offset}


@router.get("/admin/audit-logs")
def admin_audit_logs(user: dict[str, Any] = Depends(require_roles("admin")), limit: int = Query(50,ge=1,le=100), offset: int = Query(0,ge=0)):
    rows = _execute("select id,created_at,actor_id::text,action,target_type,target_id,result,details from public.admin_audit_logs order by created_at desc limit %s offset %s", (limit,offset))
    return {"items":[dict(row) for row in rows],"limit":limit,"offset":offset}


@router.get("/admin/settings")
def admin_settings(user: dict[str, Any] = Depends(require_roles("admin"))):
    rows = _execute("select key,value,updated_at from public.platform_settings where key = any(%s::text[]) order by key", (["feature_flags","supported_regions","data_retention_days","notification_settings"],))
    return {"items":[dict(row) for row in rows]}


@router.get("/admin/invitations")
def admin_invitations(user: dict[str, Any] = Depends(require_roles("admin"))):
    rows = _execute("select id::text,email,municipality_id::text,status,expires_at,created_at from public.municipal_invitations order by created_at desc limit 100")
    return {"items":[dict(row) for row in rows]}

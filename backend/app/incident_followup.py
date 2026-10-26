"""Private resident maps, municipal service deadlines and resident follow-up.

Every read keeps the reporter or active municipal/department scope explicit.
Updates lock the incident before checking its current status, and publish their
history and audit entry in the same transaction as the change.
"""
from __future__ import annotations

import json
import hashlib
import logging
from contextlib import contextmanager
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .security import postgres_connection, require_roles
from .services.notification_outbox import enqueue_notification

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["Incident follow-up"])
TERMINAL = ("resolved", "closed", "duplicate", "rejected", "unable_to_verify")
FOLLOWUP_COLUMNS = "id::text,status,response_due_at,repair_due_at,first_response_at,resolved_at,reopen_count"


class ReopenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=10, max_length=2000)


class DeadlineUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    response_due_at: AwareDatetime | None = None
    repair_due_at: AwareDatetime | None = None
    message: str = Field(min_length=5, max_length=2000)

    @model_validator(mode="after")
    def has_deadline_change(self):
        if not {"response_due_at", "repair_due_at"}.intersection(self.model_fields_set):
            raise ValueError("Provide a response or repair deadline, or null to clear it.")
        if self.response_due_at and self.repair_due_at and self.repair_due_at < self.response_due_at:
            raise ValueError("The repair deadline must not precede the response deadline.")
        return self


@contextmanager
def transaction():
    try:
        with postgres_connection() as connection:
            yield connection
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Incident follow-up database operation failed")
        raise HTTPException(503, "Incident follow-up is unavailable. Check database access and migration 017.") from exc


def _managed(connection, incident_id, user, *, lock=False):
    row = connection.execute(
        f"""select {FOLLOWUP_COLUMNS},reporter_id::text,title from public.incidents i
        where i.id=%s and i.municipality_id=any(%s::uuid[])
        and public.can_manage_incident(i.id,%s) {"for update" if lock else ""}""",
        (incident_id, user["municipality_ids"], user["id"]),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Report not found in your authorised municipality and department.")
    return row


def _audit(connection, user, incident_id, action, details):
    connection.execute(
        """insert into public.admin_audit_logs(actor_id,action,target_type,target_id,details)
        values(%s,%s,'incident',%s,%s::jsonb)""",
        (user["id"], action, str(incident_id), json.dumps(details, default=str)),
    )


@router.get("/resident/community-map")
def resident_map(limit: int = Query(250, ge=1, le=500), offset: int = Query(0, ge=0),
                 user: dict = Depends(require_roles("resident"))):
    """Only the resident's own pins; other households' exact locations stay private."""
    with transaction() as connection:
        total = connection.execute("""select count(*) as total from public.incidents
            where reporter_id=%s and location is not null""", (user["id"],)).fetchone()["total"]
        items = connection.execute("""select id::text,public_reference,title,category,status,
            location_text,created_at,extensions.st_y(location::extensions.geometry) as latitude,
            extensions.st_x(location::extensions.geometry) as longitude
            from public.incidents where reporter_id=%s and location is not null
            order by created_at desc,id desc limit %s offset %s""", (user["id"], limit, offset)).fetchall()
    return {"items": [dict(row) for row in items], "total": total, "limit": limit, "offset": offset,
            "scope": "Your reports only; municipal notices are shown separately."}


@router.get("/incidents/{incident_id}/followup")
def resident_followup(incident_id: UUID, user: dict = Depends(require_roles("resident"))):
    with transaction() as connection:
        row = connection.execute(f"""select {FOLLOWUP_COLUMNS} from public.incidents
            where id=%s and reporter_id=%s""", (incident_id, user["id"])).fetchone()
        if not row:
            raise HTTPException(404, "Report not found.")
    return {**dict(row), "can_reopen": row["status"] in ("resolved", "closed")}


@router.post("/incidents/{incident_id}/followup")
def reopen_incident(incident_id: UUID, data: ReopenRequest, user: dict = Depends(require_roles("resident"))):
    with transaction() as connection:
        row = connection.execute(f"""select {FOLLOWUP_COLUMNS},title from public.incidents
            where id=%s and reporter_id=%s for update""", (incident_id, user["id"])).fetchone()
        if not row:
            raise HTTPException(404, "Report not found.")
        if row["status"] not in ("resolved", "closed"):
            raise HTTPException(409, "Only a resolved or closed report can be reopened. Your report may already be under review.")
        updated = connection.execute(f"""update public.incidents set status='under_review',
            response_due_at=null,repair_due_at=null,first_response_at=null,resolved_at=null,
            reopen_count=reopen_count+1,updated_at=now() where id=%s and reporter_id=%s
            returning {FOLLOWUP_COLUMNS}""", (incident_id, user["id"])).fetchone()
        message = f"Resident reports that the issue is still unresolved: {data.message}"
        connection.execute("""insert into public.incident_updates(incident_id,author_id,status,message,official,internal)
            values(%s,%s,'under_review',%s,false,false)""", (incident_id, user["id"], message))
        _audit(connection, user, incident_id, "incident_reopened_by_resident",
               {"previous_status": row["status"], "reason": data.message,
                "previous_response_due_at": row["response_due_at"], "previous_repair_due_at": row["repair_due_at"]})
        connection.execute("""insert into public.notifications(resident_id,title,body,category,official)
            values(%s,%s,%s,'incident_update',false)""",
            (user["id"], f"Report reopened: {row['title']}", "Your follow-up has returned this report to municipal review. New deadlines have not yet been set."))
        enqueue_notification(connection, user["id"], f"Report reopened: {row['title']}",
            "Your follow-up has returned this report to municipal review. New deadlines have not yet been set.",
            "incident_updates", event_key=f"incident:{incident_id}:reopened:{updated['reopen_count']}")
        connection.execute("""insert into public.notifications(resident_id,title,body,category,official)
            select mm.user_id,%s,%s,'incident_update',false from public.municipal_memberships mm
            where mm.municipality_id=(select municipality_id from public.incidents where id=%s)
              and mm.status='active' and public.can_manage_incident(%s,mm.user_id)""",
            (f"Resident follow-up: {row['title']}", "The resident reports that the issue remains unresolved. Review the report and set new service deadlines.", incident_id, incident_id))
    return {**dict(updated), "can_reopen": False}


@router.get("/municipal/incidents/{incident_id}/deadlines")
def municipal_deadlines(incident_id: UUID, user: dict = Depends(require_roles("municipal"))):
    with transaction() as connection:
        row = _managed(connection, incident_id, user)
    return {key: row[key] for key in ("id", "status", "response_due_at", "repair_due_at", "first_response_at", "resolved_at", "reopen_count")}


@router.patch("/municipal/incidents/{incident_id}/deadlines")
def update_deadlines(incident_id: UUID, data: DeadlineUpdate, user: dict = Depends(require_roles("municipal"))):
    with transaction() as connection:
        row = _managed(connection, incident_id, user, lock=True)
        if row["status"] in TERMINAL:
            raise HTTPException(409, "Deadlines can only be changed on an active report.")
        response = data.response_due_at if "response_due_at" in data.model_fields_set else row["response_due_at"]
        repair = data.repair_due_at if "repair_due_at" in data.model_fields_set else row["repair_due_at"]
        if response and repair and repair < response:
            raise HTTPException(422, "The repair deadline must not precede the response deadline.")
        updated = connection.execute(f"""update public.incidents set response_due_at=%s,repair_due_at=%s,
            updated_at=now() where id=%s returning {FOLLOWUP_COLUMNS}""", (response, repair, incident_id)).fetchone()
        public_message = f"Service deadlines updated. {data.message}"
        connection.execute("""insert into public.incident_updates(incident_id,author_id,status,message,official,internal)
            values(%s,%s,%s,%s,true,false)""", (incident_id, user["id"], row["status"], public_message))
        _audit(connection, user, incident_id, "incident_deadlines_updated",
               {"previous_response_due_at": row["response_due_at"], "previous_repair_due_at": row["repair_due_at"],
                "response_due_at": response, "repair_due_at": repair, "reason": data.message})
        if row["reporter_id"]:
            connection.execute("""insert into public.notifications(resident_id,title,body,category,official)
                values(%s,%s,%s,'incident_update',true)""", (row["reporter_id"], f"Service deadlines: {row['title']}", public_message))
            fingerprint = hashlib.sha256(
                f"{response.isoformat() if response else ''}\n{repair.isoformat() if repair else ''}\n{data.message}".encode("utf-8")
            ).hexdigest()
            enqueue_notification(connection, row["reporter_id"], f"Service deadlines: {row['title']}",
                public_message, "incident_updates", event_key=f"incident:{incident_id}:deadlines:{fingerprint}")
    return dict(updated)


@router.get("/municipal/overdue")
def overdue_incidents(kind: Literal["any", "response", "repair"] = "any",
                      q: str | None = Query(None, max_length=120), category: str | None = None,
                      status: str | None = None, priority: str | None = None,
                      start_date: date | None = None, end_date: date | None = None,
                      limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
                      user: dict = Depends(require_roles("municipal"))):
    response = "(i.response_due_at<now() and i.first_response_at is null)"
    repair = "(i.repair_due_at<now())"
    overdue = response if kind == "response" else repair if kind == "repair" else f"({response} or {repair})"
    where = f"""i.municipality_id=any(%s::uuid[]) and public.can_manage_incident(i.id,%s)
        and i.status not in ('resolved','closed','duplicate','rejected','unable_to_verify') and {overdue}"""
    params: list = [user["municipality_ids"], user["id"]]
    for field, value in (("category", category), ("status", status), ("priority", priority)):
        if value:
            where += f" and i.{field}=%s"
            params.append(value)
    if start_date:
        where += " and i.created_at::date>=%s"
        params.append(start_date)
    if end_date:
        where += " and i.created_at::date<=%s"
        params.append(end_date)
    if q:
        where += " and (i.title ilike %s or i.description ilike %s or i.location_text ilike %s or i.public_reference ilike %s)"
        params.extend([f"%{q}%"] * 4)
    with transaction() as connection:
        total = connection.execute(f"select count(*) as total from public.incidents i where {where}", tuple(params)).fetchone()["total"]
        items = connection.execute(f"""select i.id::text,i.public_reference,i.title,i.description,i.category,
            i.status,i.priority,i.location_text as location,i.created_at,i.response_due_at,i.repair_due_at,
            i.first_response_at,i.resolved_at,i.reopen_count,
            {response} as response_overdue,{repair} as repair_overdue
            from public.incidents i where {where}
            order by least(i.response_due_at,i.repair_due_at) asc,i.created_at asc,i.id
            limit %s offset %s""", tuple(params + [limit, offset])).fetchall()
    return {"items": [dict(row) for row in items], "total": total, "limit": limit, "offset": offset}

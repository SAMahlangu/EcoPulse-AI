from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .security import current_user, postgres_connection, require_roles
from .services.notification_outbox import enqueue_alert_notifications

router = APIRouter(prefix="/api/v1/service-alerts", tags=["service alerts"])


class AlertCreate(BaseModel):
    incident_id: UUID
    service_type: Literal["electricity", "water", "transport"]
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=10, max_length=3000)
    community_ids: list[UUID] = Field(default_factory=list, max_length=100)
    disruption_type: Literal["planned", "unplanned", "emergency"]
    severity: Literal["low", "medium", "high", "critical"]
    starts_at: datetime
    estimated_restoration_at: datetime | None = None


class RepairStep(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    description: str = Field(min_length=3, max_length=1000)
    status: Literal["pending", "in_progress", "completed", "blocked"] = "pending"
    responsible_team: str | None = Field(default=None, max_length=120)
    position: int = Field(ge=1)


class StepUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, min_length=3, max_length=1000)
    status: Literal["pending", "in_progress", "completed", "blocked"] | None = None
    responsible_team: str | None = Field(default=None, max_length=120)


class Feedback(BaseModel):
    response: Literal["yes", "no", "not_sure"]


def _alert_for_resident(con, alert_id: str, user_id: str, lock: bool = False):
    return con.execute(f"""select a.* from public.service_alerts a
      where a.id=%s and a.published_at is not null and exists(
        select 1 from public.profiles p where p.id=%s and (
          (a.target_scope='municipality' and p.municipality_id=a.municipality_id)
          or (a.target_scope='community' and exists(
            select 1 from public.service_alert_areas aa where aa.alert_id=a.id and aa.community_id=p.community_id))))
      {'for update' if lock else ''}""", (alert_id, user_id)).fetchone()


def _alert_for_official(con, alert_id: str, user: dict[str, Any], lock: bool = False):
    row = con.execute(f"""select a.* from public.service_alerts a where a.id=%s
      and a.municipality_id=any(%s::uuid[]) and exists(
        select 1 from public.municipal_memberships mm join public.profiles p on p.id=mm.user_id
        where mm.user_id=%s and mm.municipality_id=a.municipality_id and mm.status='active'
          and p.role='municipal' and p.account_status='active')
      and public.can_manage_incident(a.incident_id,%s) {'for update' if lock else ''}""",
        (alert_id, user.get("municipality_ids") or [], user["id"], user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "Service alert not found in your authorised municipality.")
    return row


@router.get("")
def list_alerts(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0), user: dict[str, Any] = Depends(current_user)):
    with postgres_connection() as con:
        if user["role"] == "resident":
            rows = con.execute("""select a.id::text,a.service_type,a.title,a.description,a.disruption_type,a.severity,a.status,a.target_scope,a.starts_at,a.estimated_restoration_at,a.published_at,a.created_at,m.name as municipality,
              coalesce(array_agg(jsonb_build_object('id',c.id::text,'name',c.name)) filter(where c.id is not null),'{}') as areas
              from public.service_alerts a join public.municipalities m on m.id=a.municipality_id
              left join public.service_alert_areas aa on aa.alert_id=a.id left join public.communities c on c.id=aa.community_id
              where a.published_at is not null and a.status<>'cancelled' and exists(select 1 from public.profiles p where p.id=%s and (
                (a.target_scope='municipality' and p.municipality_id=a.municipality_id)
                or (a.target_scope='community' and exists(select 1 from public.service_alert_areas ra where ra.alert_id=a.id and ra.community_id=p.community_id))))
              group by a.id,m.name order by a.published_at desc limit %s offset %s""", (user["id"], limit, offset)).fetchall()
        elif user["role"] == "municipal":
            rows = con.execute("""select a.id::text,a.incident_id::text,a.service_type,a.title,a.description,a.disruption_type,a.severity,a.status,a.target_scope,a.starts_at,a.estimated_restoration_at,a.published_at,a.created_at,m.name as municipality,
              coalesce(array_agg(jsonb_build_object('id',c.id::text,'name',c.name)) filter(where c.id is not null),'{}') as areas
              from public.service_alerts a join public.municipalities m on m.id=a.municipality_id left join public.service_alert_areas aa on aa.alert_id=a.id left join public.communities c on c.id=aa.community_id
              where a.municipality_id=any(%s::uuid[]) and public.can_manage_incident(a.incident_id,%s)
              group by a.id,m.name order by a.created_at desc limit %s offset %s""", (user["municipality_ids"], user["id"], limit, offset)).fetchall()
        else:
            rows = con.execute("""select a.id::text,a.incident_id::text,a.service_type,a.title,a.description,a.disruption_type,a.severity,a.status,a.target_scope,a.starts_at,a.estimated_restoration_at,a.published_at,a.created_at,m.name as municipality,
              coalesce(array_agg(jsonb_build_object('id',c.id::text,'name',c.name)) filter(where c.id is not null),'{}') as areas
              from public.service_alerts a join public.municipalities m on m.id=a.municipality_id left join public.service_alert_areas aa on aa.alert_id=a.id left join public.communities c on c.id=aa.community_id
              group by a.id,m.name order by a.created_at desc limit %s offset %s""", (limit, offset)).fetchall()
    return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}


@router.get("/communities")
def selectable_communities(user: dict[str, Any] = Depends(require_roles("municipal"))):
    with postgres_connection() as con:
        rows = con.execute("select id::text,municipality_id::text,name from public.communities where municipality_id=any(%s::uuid[]) order by name", (user["municipality_ids"],)).fetchall()
    return {"items": [dict(r) for r in rows]}


@router.post("", status_code=201)
def create_alert(body: AlertCreate, user: dict[str, Any] = Depends(require_roles("municipal"))):
    ids = list(dict.fromkeys(str(v) for v in body.community_ids))
    with postgres_connection() as con:
        incident = con.execute("select id::text,municipality_id::text from public.incidents where id=%s and municipality_id=any(%s::uuid[]) and public.can_manage_incident(id,%s)", (str(body.incident_id), user["municipality_ids"], user["id"])).fetchone()
        if not incident:
            raise HTTPException(404, "Incident not found in your authorised municipality.")
        allowed = con.execute("select id::text from public.communities where id=any(%s::uuid[]) and municipality_id=%s", (ids, incident["municipality_id"])).fetchall()
        if len(allowed) != len(ids):
            raise HTTPException(403, "Every selected community must belong to the incident municipality.")
        row = con.execute("""insert into public.service_alerts(municipality_id,incident_id,author_id,service_type,title,description,disruption_type,severity,starts_at,estimated_restoration_at,target_scope)
          values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id::text""", (incident["municipality_id"],str(body.incident_id),user["id"],body.service_type,body.title.strip(),body.description.strip(),body.disruption_type,body.severity,body.starts_at,body.estimated_restoration_at,'community' if ids else 'municipality')).fetchone()
        if ids:
            with con.cursor() as cursor:
                cursor.executemany("insert into public.service_alert_areas(alert_id,community_id) values(%s,%s)", [(row["id"], cid) for cid in ids])
    return {"id": row["id"], "status": "draft"}


@router.post("/{alert_id}/publish")
def publish_alert(alert_id: UUID, user: dict[str, Any] = Depends(require_roles("municipal"))):
    with postgres_connection() as con:
        alert = _alert_for_official(con, str(alert_id), user, True)
        if alert["status"] != "draft":
            raise HTTPException(409, "Only draft alerts can be published.")
        con.execute("update public.service_alerts set status=case when starts_at>now() then 'scheduled' else 'in_progress' end,published_at=now(),updated_at=now() where id=%s", (str(alert_id),))
        con.execute("""insert into public.notifications(resident_id,title,body,category,official)
          select distinct p.id,%s,%s,'service_alert',true from public.service_alerts alert
          join public.profiles p on p.role='resident' and coalesce(p.account_status,'active')='active' and ((alert.target_scope='municipality' and p.municipality_id=alert.municipality_id)
            or (alert.target_scope='community' and exists(select 1 from public.service_alert_areas aa where aa.alert_id=alert.id and aa.community_id=p.community_id)))
          where alert.id=%s and not exists(select 1 from public.notifications n where n.resident_id=p.id and n.category='service_alert' and n.body like %s)""",
          (f"{alert['service_type'].upper()} ALERT: {alert['title']}", f"{alert['description']} View community service updates. [alert:{alert_id}]", str(alert_id), f"%[alert:{alert_id}]%"))
        email_subject = f"{alert['service_type'].upper()} service alert: {alert['title']}"
        email_body = f"{alert['description']}\n\nThis alert applies to residents in your municipality. View service updates in your EcoPulse resident dashboard."
        recipient_count = enqueue_alert_notifications(con,str(alert_id),email_subject,email_body,event_key=f"service-alert:{alert_id}:published")
    return {"id": str(alert_id), "status": "published", "notification_recipient_count": recipient_count}


@router.get("/{alert_id}")
def alert_details(alert_id: UUID, user: dict[str, Any] = Depends(current_user)):
    with postgres_connection() as con:
        if user["role"] == "municipal":
            alert = _alert_for_official(con, str(alert_id), user)
        elif user["role"] == "resident":
            alert = _alert_for_resident(con, str(alert_id), user["id"])
            if not alert: raise HTTPException(404, "Service alert not found.")
        else:
            alert = con.execute("select * from public.service_alerts where id=%s", (str(alert_id),)).fetchone()
            if not alert: raise HTTPException(404, "Service alert not found.")
        areas = con.execute("select c.id::text,c.name from public.service_alert_areas aa join public.communities c on c.id=aa.community_id where aa.alert_id=%s order by c.name", (str(alert_id),)).fetchall()
        steps = con.execute("select id::text,title,description,status,responsible_team,position,updated_at from public.service_alert_repair_steps where alert_id=%s order by position", (str(alert_id),)).fetchall()
        updates = con.execute("select id::text,message,created_at from public.service_alert_updates where alert_id=%s order by created_at desc limit 50", (str(alert_id),)).fetchall()
    result = dict(alert)
    result.update(areas=[dict(r) for r in areas], repair_steps=[dict(r) for r in steps], updates=[dict(r) for r in updates])
    return result


@router.post("/{alert_id}/repair-plan", status_code=201)
def create_repair_step(alert_id: UUID, body: RepairStep, user: dict[str, Any] = Depends(require_roles("municipal"))):
    with postgres_connection() as con:
        alert = _alert_for_official(con, str(alert_id), user)
        row = con.execute("""insert into public.service_alert_repair_steps(alert_id,title,description,status,responsible_team,position)
          values(%s,%s,%s,%s,%s,%s) returning id::text,title,description,status,responsible_team,position,updated_at""", (str(alert_id),body.title,body.description,body.status,body.responsible_team,body.position)).fetchone()
        if alert["published_at"]:
            enqueue_alert_notifications(con,str(alert_id),f"Repair progress: {alert['title']}",
              f"A public repair stage was added: {body.title}. {body.description}",event_key=f"repair-step:{row['id']}:created")
    return dict(row)


@router.patch("/{alert_id}/repair-plan/{step_id}")
def update_repair_step(alert_id: UUID, step_id: UUID, body: StepUpdate, user: dict[str, Any] = Depends(require_roles("municipal"))):
    fields = body.model_dump(exclude_unset=True)
    if not fields: raise HTTPException(422, "Provide at least one repair step field.")
    with postgres_connection() as con:
        alert = _alert_for_official(con, str(alert_id), user)
        cols = {"title", "description", "status", "responsible_team"}
        if not set(fields) <= cols: raise HTTPException(422, "Invalid repair step fields.")
        assignments = ",".join(f"{key}=%s" for key in fields)
        row = con.execute(f"update public.service_alert_repair_steps set {assignments},updated_at=now() where id=%s and alert_id=%s returning id::text,title,description,status,responsible_team,position,updated_at", (*fields.values(),str(step_id),str(alert_id))).fetchone()
        if not row: raise HTTPException(404, "Repair step not found.")
        if alert["published_at"]:
            enqueue_alert_notifications(con,str(alert_id),f"Repair progress: {alert['title']}",
              f"Repair stage update: {row['title']} is now {row['status'].replace('_',' ')}. {row['description']}",
              event_key=f"repair-step:{row['id']}:{row['updated_at']}")
    return dict(row)


@router.post("/{alert_id}/updates", status_code=201)
def add_public_update(alert_id: UUID, message: str = Query(..., min_length=3, max_length=2000), user: dict[str, Any] = Depends(require_roles("municipal"))):
    with postgres_connection() as con:
        alert = _alert_for_official(con, str(alert_id), user)
        row = con.execute("insert into public.service_alert_updates(alert_id,author_id,message) values(%s,%s,%s) returning id::text,message,created_at", (str(alert_id),user["id"],message.strip())).fetchone()
        if alert["published_at"]:
            enqueue_alert_notifications(con,str(alert_id),f"Service alert update: {alert['title']}",message.strip(),
                                        event_key=f"service-alert-update:{row['id']}")
    return dict(row)


class AlertStatus(BaseModel):
    status: Literal["in_progress", "restored", "cancelled"]
    estimated_restoration_at: datetime | None = None


@router.patch("/{alert_id}")
def update_alert(alert_id: UUID, body: AlertStatus, user: dict[str, Any] = Depends(require_roles("municipal"))):
    with postgres_connection() as con:
        alert = _alert_for_official(con, str(alert_id), user, True)
        row = con.execute("update public.service_alerts set status=%s,estimated_restoration_at=%s,updated_at=now() where id=%s and published_at is not null returning id::text,status,estimated_restoration_at,updated_at", (body.status,body.estimated_restoration_at,str(alert_id))).fetchone()
        if not row:
            raise HTTPException(409,"Publish the alert before changing its service status.")
        estimate = f" Estimated restoration: {row['estimated_restoration_at']}." if row["estimated_restoration_at"] else ""
        enqueue_alert_notifications(con,str(alert_id),f"Service alert update: {alert['title']}",
          f"Service status: {row['status'].replace('_',' ')}.{estimate}",event_key=f"service-alert:{alert_id}:{row['updated_at']}")
    return dict(row)


@router.post("/{alert_id}/feedback")
def submit_feedback(alert_id: UUID, body: Feedback, user: dict[str, Any] = Depends(require_roles("resident"))):
    with postgres_connection() as con:
        alert = con.execute("select service_type,status from public.service_alerts where id=%s and published_at is not null and service_type in ('electricity','water') and exists(select 1 from public.service_alert_areas aa join public.profiles p on p.community_id=aa.community_id where aa.alert_id=%s and p.id=%s)", (str(alert_id),str(alert_id),user["id"])).fetchone()
        if not alert: raise HTTPException(404, "Restoration feedback is not available for this alert.")
        if alert["status"] != "restored": raise HTTPException(409, "Feedback is available after the municipality marks the service restored.")
        row = con.execute("""insert into public.service_alert_feedback(alert_id,resident_id,response) values(%s,%s,%s)
          on conflict(alert_id,resident_id) do update set response=excluded.response,updated_at=now() returning response,updated_at""", (str(alert_id),user["id"],body.response)).fetchone()
    return dict(row)


@router.get("/{alert_id}/feedback-summary")
def feedback_summary(alert_id: UUID, user: dict[str, Any] = Depends(require_roles("municipal"))):
    with postgres_connection() as con:
        _alert_for_official(con, str(alert_id), user)
        rows = con.execute("select response,count(*)::int as count from public.service_alert_feedback where alert_id=%s group by response", (str(alert_id),)).fetchall()
    counts = {r["response"]: r["count"] for r in rows}
    return {"total": sum(counts.values()), "yes": counts.get("yes",0), "no": counts.get("no",0), "not_sure": counts.get("not_sure",0), "note": "Voluntary resident feedback; it does not verify service coverage."}

"""Administrator-only email operations; never exposes recipient addresses or message bodies."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from .security import postgres_connection, require_roles

router = APIRouter(prefix="/api/v1/admin/notification-delivery", tags=["notification delivery"])


@router.get("")
def list_deliveries(status: Literal["pending", "processing", "retry", "accepted", "failed", "skipped"] | None = None,
                    limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
                    user=Depends(require_roles("admin"))):
    with postgres_connection() as con:
        rows = con.execute("""select id::text,user_id::text,preference,status,attempts,max_attempts,
          next_attempt_at,provider,provider_message_id,last_error_code,accepted_at,manual_retries,created_at,updated_at
          from public.notification_outbox where (%s::text is null or status=%s)
          order by created_at desc,id desc limit %s offset %s""", (status,status,limit,offset)).fetchall()
        total = con.execute("select count(*)::int as count from public.notification_outbox where (%s::text is null or status=%s)",
                            (status,status)).fetchone()["count"]
        counts = con.execute("select status,count(*)::int as count from public.notification_outbox group by status").fetchall()
    return {"items": rows,"total": total,"counts": {row["status"]:row["count"] for row in counts},
            "limit":limit,"offset":offset,"delivery_confirmation_available":False}


@router.post("/{job_id}/retry")
def retry_delivery(job_id: UUID, user=Depends(require_roles("admin"))):
    with postgres_connection() as con:
        job = con.execute("""update public.notification_outbox set status='pending',attempts=0,next_attempt_at=now(),
          last_error_code=null,manual_retries=manual_retries+1,updated_at=now()
          where id=%s and status in ('failed','skipped') returning id::text,status""", (str(job_id),)).fetchone()
        if not job:
            raise HTTPException(409,"Only failed or skipped deliveries can be retried. Accepted email cannot be resent here.")
        con.execute("""insert into public.admin_audit_logs(actor_id,action,target_type,target_id,details)
          values(%s,'notification_delivery_retried','notification_outbox',%s,'{}'::jsonb)""", (user["id"],str(job_id)))
    return job

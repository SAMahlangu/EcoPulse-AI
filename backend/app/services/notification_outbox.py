"""Transactional enqueueing and leased delivery; no external I/O in DB transactions."""
from __future__ import annotations

import uuid
from typing import Any

from ..email_notifications import DeliveryResult, send_user_notification_email
from ..security import postgres_connection

PREFERENCES = {"incident_updates", "municipal_notices", "climate_alerts"}


def enqueue_notification(con, user_id: str, subject: str, body: str, preference: str,
                         *, event_key: str) -> str | None:
    """Call on the SAME connection/transaction as the originating mutation."""
    if preference not in PREFERENCES or not 1 <= len(event_key) <= 250:
        raise ValueError("Invalid notification preference or event key")
    row = con.execute("""insert into public.notification_outbox(user_id,event_key,subject,body,preference)
      values(%s,%s,%s,%s,%s) on conflict(user_id,event_key) do nothing returning id::text""",
      (str(user_id), event_key, subject[:180], body[:10000], preference)).fetchone()
    return row["id"] if row else None


def enqueue_alert_notifications(con, alert_id: str, subject: str, body: str, *, event_key: str) -> int:
    """Bulk enqueue municipality/community recipients without loading them into Python."""
    if not 1 <= len(event_key) <= 250:
        raise ValueError("Invalid notification event key")
    row = con.execute("""with queued as (
      insert into public.notification_outbox(user_id,event_key,subject,body,preference)
      select p.id,%s,%s,%s,'municipal_notices' from public.profiles p
      join public.service_alerts a on a.id=%s
      where a.published_at is not null and p.role='resident' and coalesce(p.account_status,'active')='active'
        and ((a.target_scope='municipality' and p.municipality_id=a.municipality_id)
          or (a.target_scope='community' and exists(select 1 from public.service_alert_areas aa
            where aa.alert_id=a.id and aa.community_id=p.community_id)))
      on conflict(user_id,event_key) do nothing returning id)
      select count(*)::int as count from queued""", (event_key, subject[:180], body[:10000], str(alert_id))).fetchone()
    return int(row["count"])


def retry_delay(attempt: int, retry_after_seconds: int | None = None) -> int:
    return max(min(3600, 30 * 2 ** max(0, min(attempt - 1, 10))), min(3600, retry_after_seconds or 0))


def claim_notification() -> dict[str, Any] | None:
    token = str(uuid.uuid4())
    with postgres_connection() as con:
        # A crashed worker's last attempt must not leave a job stuck indefinitely.
        con.execute("""update public.notification_outbox set
          status=case when attempts>=max_attempts then 'failed' else 'retry' end,
          next_attempt_at=now(),claim_token=null,lease_until=null,last_error_code='worker_lease_expired',updated_at=now()
          where status='processing' and lease_until<=now()""")
        return con.execute("""with candidate as (
          select id from public.notification_outbox where status in ('pending','retry')
            and next_attempt_at<=now() and attempts<max_attempts
          order by next_attempt_at,created_at for update skip locked limit 1)
          update public.notification_outbox o set status='processing',attempts=o.attempts+1,
            claim_token=%s,lease_until=now()+interval '5 minutes',updated_at=now()
          from candidate where o.id=candidate.id
          returning o.id::text,o.user_id::text,o.subject,o.body,o.preference,o.attempts,o.max_attempts,o.claim_token::text""",
          (token,)).fetchone()


def finish_notification(job: dict[str, Any], result: DeliveryResult) -> None:
    status = result.status
    if status not in {"accepted", "skipped", "retry", "failed"}:
        status = "retry"
    if status == "retry" and job["attempts"] >= job["max_attempts"]:
        status = "failed"
    delay = retry_delay(job["attempts"], result.retry_after_seconds)
    with postgres_connection() as con:
        con.execute("""update public.notification_outbox set status=%s,provider=%s,provider_message_id=%s,
          last_error_code=%s,next_attempt_at=now()+(%s * interval '1 second'),
          accepted_at=case when %s='accepted' then now() else accepted_at end,
          lease_until=null,claim_token=null,updated_at=now()
          where id=%s and status='processing' and claim_token=%s""",
          (status,result.provider,result.provider_message_id,result.error_code,delay,status,job["id"],job["claim_token"]))


def deliver_one() -> bool:
    job = claim_notification()
    if not job:
        return False
    result = send_user_notification_email(job["user_id"],job["subject"],job["body"],job["preference"],
                                          idempotency_key=f"notification:{job['id']}")
    finish_notification(job, result)
    return True


def activate_scheduled_alerts() -> int:
    """Advance due published alerts; workers serialize each alert with row locks."""
    with postgres_connection() as con:
        rows = con.execute("""with due as (
          select id from public.service_alerts where status='scheduled' and published_at is not null
            and starts_at<=now() order by starts_at for update skip locked limit 50)
          update public.service_alerts a set status='in_progress',updated_at=now() from due where a.id=due.id
          returning a.id::text,a.title""").fetchall()
        for alert in rows:
            enqueue_alert_notifications(con,alert["id"],f"Service alert started: {alert['title']}",
              "The published disruption has reached its scheduled start. View your service updates for progress and restoration estimates.",
              event_key=f"service-alert:{alert['id']}:scheduled-start")
        return len(rows)

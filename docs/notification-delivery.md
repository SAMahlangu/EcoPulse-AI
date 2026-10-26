# Durable notification delivery

Apply `database/migrations/016_notification_outbox.sql` using the reviewed migration process before enabling this version. Incident changes and service alert updates insert email jobs on the same PostgreSQL connection and transaction as the originating change. A rollback removes both the change and its delivery jobs. Stable event keys deduplicate each event per resident. Municipality-wide alerts use one `INSERT ... SELECT`; recipients are not loaded into the API process.

The API starts a worker when `DATABASE_URL` is configured. Set `ECOPULSE_NOTIFICATION_WORKER_ENABLED=false` in processes that must not send mail (including isolated automated tests), or when running only a separate worker. `ECOPULSE_NOTIFICATION_POLL_SECONDS` defaults to 5 and is bounded to 1–60 seconds. The worker claims one due job with `FOR UPDATE SKIP LOCKED`, commits its claim before contacting the provider, and leases it for five minutes. Other API processes can safely run workers concurrently. Expired leases are recovered after process crashes. Each job has at most six attempts, with exponential retry delays starting at 30 seconds and capped at one hour. Transient provider errors retry; permanent rejections become failed. Administrator retries are explicit, audited, and restart the attempt counter.

For a separate worker, use a second CMD window from the repository root:

```cmd
cd /d C:\Users\student\Desktop\FNB
backend\venv\Scripts\python.exe -m backend.app.services.notification_worker
```

This command reads `backend/.env`. Existing backend-only `RESEND_API_KEY` / `RESEND_FROM` or `SMTP_*` configuration is reused. Account status, resident role, current email address, and notification preference are checked immediately before each send. Opted-out, removed, or inactive recipients are recorded as skipped. Provider credentials, recipient addresses, and email bodies are never written by the delivery logger. Outbox bodies remain private backend data and must be covered by the deployment’s retention and backup policy.

Delivery states are **queued**, **sending**, **retry scheduled**, **provider accepted**, **failed**, and **skipped**. Resend acceptance and SMTP acceptance do not prove delivery to an inbox. No delivery/bounce webhook is installed; the administrator screen says this explicitly and retains provider message IDs for investigation. Resend requests use a stable idempotency key. SMTP uses a stable Message-ID, but SMTP does not guarantee deduplication: a crash after provider acceptance and before saving the result can result in a duplicate on retry. This is an at-least-once delivery queue, not an exactly-once promise.

The administrator **Email delivery** page uses `GET /api/v1/admin/notification-delivery` and `POST /api/v1/admin/notification-delivery/{id}/retry`. Only failed/skipped jobs may be manually retried. Neither API exposes message bodies or recipient email addresses. Accepted email cannot be resent from that API. The table has RLS enabled and grants no browser-role access; the backend checks administrator authorization before querying it.

The same worker advances published service alerts from scheduled to in progress after their start time and atomically queues the start notice. This does not assert that a repair actually started or that restoration is complete; residents receive the published disruption’s scheduled-start notice. Restoration remains a municipal action. Stopping the worker delays automatic start transitions and email delivery until it resumes.

Before deployment, run the backend delivery tests and verify transactional enqueueing, concurrent claims, lease recovery, and opt-out handling against a development PostgreSQL database. Start with a verified sender and an owned test mailbox. Do not point mail tests at resident accounts.

-- Durable application email delivery. Apply after the existing application schema.
begin;
create table if not exists public.notification_outbox (
 id uuid primary key default gen_random_uuid(),
 user_id uuid not null references public.profiles(id) on delete cascade,
 event_key text not null check(length(event_key) between 1 and 250),
 subject text not null check(length(subject) between 1 and 180),
 body text not null check(length(body) <= 10000),
 preference text not null check(preference in ('incident_updates','municipal_notices','climate_alerts')),
 status text not null default 'pending' check(status in ('pending','processing','retry','accepted','failed','skipped')),
 attempts integer not null default 0 check(attempts >= 0),
 max_attempts integer not null default 6 check(max_attempts between 1 and 20),
 next_attempt_at timestamptz not null default now(),
 lease_until timestamptz,
 claim_token uuid,
 provider text,
 provider_message_id text,
 last_error_code text,
 accepted_at timestamptz,
 manual_retries integer not null default 0 check(manual_retries >= 0),
 created_at timestamptz not null default now(),
 updated_at timestamptz not null default now(),
 unique(user_id,event_key),
 check((status='processing')=(lease_until is not null and claim_token is not null))
);
create index if not exists notification_outbox_due_idx on public.notification_outbox(next_attempt_at,created_at)
 where status in ('pending','retry');
create index if not exists notification_outbox_lease_idx on public.notification_outbox(lease_until)
 where status='processing';
create index if not exists notification_outbox_status_created_idx on public.notification_outbox(status,created_at desc);
alter table public.notification_outbox enable row level security;
-- Delivery payloads are backend-only, including for administrator browser sessions.
revoke all on public.notification_outbox from anon,authenticated;
grant all on public.notification_outbox to service_role;
comment on table public.notification_outbox is 'Durable email queue. accepted means provider accepted, not delivered. Email payloads must follow the configured retention policy.';
commit;

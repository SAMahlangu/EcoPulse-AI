-- Shared municipal service alerts, public repair progress, and voluntary feedback.
begin;
create table if not exists public.service_alerts (
  id uuid primary key default gen_random_uuid(), municipality_id uuid not null references public.municipalities(id),
  incident_id uuid not null references public.incidents(id), author_id uuid not null references public.profiles(id),
  service_type text not null check(service_type in ('electricity','water','transport')),
  title text not null check(length(title) between 3 and 120), description text not null check(length(description) between 10 and 3000),
  disruption_type text not null check(disruption_type in ('planned','unplanned','emergency')),
  severity text not null check(severity in ('low','medium','high','critical')),
  status text not null default 'draft' check(status in ('draft','scheduled','in_progress','restored','cancelled')),
  starts_at timestamptz not null, estimated_restoration_at timestamptz, published_at timestamptz,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index if not exists service_alerts_municipality_idx on public.service_alerts(municipality_id,created_at desc);
create index if not exists service_alerts_incident_idx on public.service_alerts(incident_id);
create index if not exists service_alerts_status_idx on public.service_alerts(status,published_at desc);
create index if not exists service_alerts_service_idx on public.service_alerts(service_type,status);
create table if not exists public.service_alert_areas (
  alert_id uuid not null references public.service_alerts(id) on delete cascade,
  community_id uuid not null references public.communities(id), primary key(alert_id,community_id)
);
create index if not exists service_alert_areas_community_idx on public.service_alert_areas(community_id,alert_id);
create table if not exists public.service_alert_updates (
  id uuid primary key default gen_random_uuid(), alert_id uuid not null references public.service_alerts(id) on delete cascade,
  author_id uuid not null references public.profiles(id), message text not null check(length(message) between 3 and 2000),
  created_at timestamptz not null default now()
);
create index if not exists service_alert_updates_alert_idx on public.service_alert_updates(alert_id,created_at);
create table if not exists public.service_alert_repair_steps (
  id uuid primary key default gen_random_uuid(), alert_id uuid not null references public.service_alerts(id) on delete cascade,
  title text not null check(length(title) between 2 and 120), description text not null check(length(description) between 3 and 1000),
  status text not null default 'pending' check(status in ('pending','in_progress','completed','blocked')),
  responsible_team text check(length(responsible_team)<=120), position integer not null check(position>0),
  updated_at timestamptz not null default now(), created_at timestamptz not null default now(), unique(alert_id,position)
);
create index if not exists service_alert_steps_alert_idx on public.service_alert_repair_steps(alert_id,position);
create table if not exists public.service_alert_feedback (
  alert_id uuid not null references public.service_alerts(id) on delete cascade,
  resident_id uuid not null references public.profiles(id) on delete cascade,
  response text not null check(response in ('yes','no','not_sure')),
  updated_at timestamptz not null default now(), primary key(alert_id,resident_id)
);
create index if not exists service_alert_feedback_alert_idx on public.service_alert_feedback(alert_id,response);
alter table public.service_alerts enable row level security;
alter table public.service_alert_areas enable row level security;
alter table public.service_alert_updates enable row level security;
alter table public.service_alert_repair_steps enable row level security;
alter table public.service_alert_feedback enable row level security;
-- All writes and reads pass through FastAPI with verified bearer identity and service-role DB access.
revoke all on public.service_alerts,public.service_alert_areas,public.service_alert_updates,public.service_alert_repair_steps,public.service_alert_feedback from anon,authenticated;
commit;

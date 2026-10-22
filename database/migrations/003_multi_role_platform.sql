-- Additive platform expansion. Apply only after reviewing existing schema and
-- confirming public.profiles, public.municipalities, public.incidents exist.
alter table public.profiles
  add column if not exists account_status text not null default 'active'
    check (account_status in ('active','suspended'));

alter table public.incidents
  add column if not exists municipality_id uuid references public.municipalities(id),
  add column if not exists location_text text not null default '',
  add column if not exists priority text not null default 'normal'
    check (priority in ('low','normal','high','urgent')),
  add column if not exists resolution_note text;
alter table public.incidents drop constraint if exists incidents_status_check;
alter table public.incidents add constraint incidents_status_check
  check(status in ('submitted','acknowledged','under_review','verified','in_progress','resolved','closed','duplicate','rejected','unable_to_verify'));
alter table public.incident_updates add column if not exists internal boolean not null default false;
create index if not exists incidents_municipality_status_idx
  on public.incidents(municipality_id,status,created_at desc);
create index if not exists incidents_priority_idx on public.incidents(priority,created_at desc);
alter table public.municipalities add column if not exists is_demonstration boolean not null default true;

create table if not exists public.municipal_memberships (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  municipality_id uuid not null references public.municipalities(id) on delete cascade,
  status text not null default 'pending' check(status in ('pending','active','revoked')),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  unique(user_id,municipality_id)
);
create index if not exists municipal_memberships_user_status_idx
  on public.municipal_memberships(user_id,status);
create index if not exists municipal_memberships_municipality_idx
  on public.municipal_memberships(municipality_id,status);

create table if not exists public.municipal_invitations (
  id uuid primary key default gen_random_uuid(),
  email text not null, municipality_id uuid not null references public.municipalities(id),
  invited_by uuid not null references public.profiles(id), token_hash text not null unique,
  status text not null default 'pending' check(status in ('pending','accepted','revoked','expired')),
  expires_at timestamptz not null, created_at timestamptz not null default now()
);
create index if not exists municipal_invitations_email_status_idx
  on public.municipal_invitations(lower(email),status);

create table if not exists public.incident_assignments (
  id uuid primary key default gen_random_uuid(), incident_id uuid not null references public.incidents(id) on delete cascade,
  assigned_to uuid not null references public.profiles(id), assigned_by uuid not null references public.profiles(id),
  created_at timestamptz not null default now(), unique(incident_id,assigned_to)
);
create index if not exists incident_assignments_user_idx on public.incident_assignments(assigned_to,created_at desc);

create table if not exists public.municipal_notices (
  id uuid primary key default gen_random_uuid(), municipality_id uuid not null references public.municipalities(id),
  author_id uuid not null references public.profiles(id), category text not null
    check(category in ('water','electricity','road','maintenance','emergency')),
  title text not null check(length(title) between 3 and 120), body text not null check(length(body) between 10 and 4000),
  status text not null default 'draft' check(status in ('draft','published','archived')),
  starts_at timestamptz, ends_at timestamptz, demonstration boolean not null default true check(demonstration=true),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index if not exists municipal_notices_municipality_status_idx
  on public.municipal_notices(municipality_id,status,created_at desc);

create table if not exists public.contact_enquiries (
  id uuid primary key default gen_random_uuid(), full_name text not null,
  email text not null, organisation text, subject text not null, message text not null,
  created_at timestamptz not null default now()
);
create index if not exists contact_enquiries_created_idx on public.contact_enquiries(created_at desc);

create table if not exists public.admin_audit_logs (
  id bigserial primary key, actor_id uuid references public.profiles(id), action text not null,
  target_type text not null, target_id text, result text not null default 'success', details jsonb not null default '{}',
  created_at timestamptz not null default now()
);
create index if not exists admin_audit_logs_created_idx on public.admin_audit_logs(created_at desc);
create index if not exists admin_audit_logs_actor_idx on public.admin_audit_logs(actor_id,created_at desc);

create table if not exists public.platform_settings (
  key text primary key, value jsonb not null default '{}', updated_by uuid references public.profiles(id),
  updated_at timestamptz not null default now()
);

create or replace function public.is_platform_admin() returns boolean
language sql stable security definer set search_path=public as $$
  select exists(select 1 from public.profiles p where p.id=auth.uid() and p.role='admin' and p.account_status='active');
$$;
create or replace function public.is_municipal_member(target_municipality uuid) returns boolean
language sql stable security definer set search_path=public as $$
  select exists(select 1 from public.municipal_memberships m join public.profiles p on p.id=m.user_id
    where m.user_id=auth.uid() and m.municipality_id=target_municipality and m.status='active'
      and p.role='municipal' and p.account_status='active');
$$;
create or replace function public.protect_profile_role() returns trigger language plpgsql as $$
begin
  if new.role is distinct from old.role
     and coalesce(auth.role(),'') <> 'service_role'
     and current_user not in ('postgres','supabase_admin') then
    raise exception 'Profile role is managed by trusted services';
  end if;
  return new;
end; $$;
-- The row policy alone is not enough: residents must not rewrite trusted
-- assignment, suspension, or role columns in their own profile row.
revoke update on public.profiles from anon, authenticated;
grant update(full_name,household_size,notification_preferences) on public.profiles to authenticated;

alter table public.municipal_memberships enable row level security;
alter table public.municipal_invitations enable row level security;
alter table public.incident_assignments enable row level security;
alter table public.municipal_notices enable row level security;
alter table public.contact_enquiries enable row level security;
alter table public.admin_audit_logs enable row level security;
alter table public.platform_settings enable row level security;

drop policy if exists "Municipal users read own memberships" on public.municipal_memberships;
create policy "Municipal users read own memberships" on public.municipal_memberships for select
  using (user_id=auth.uid() or public.is_platform_admin());
drop policy if exists "Admins manage memberships" on public.municipal_memberships;
create policy "Admins manage memberships" on public.municipal_memberships for all
  using (public.is_platform_admin()) with check (public.is_platform_admin());
drop policy if exists "Municipal users read assigned incidents" on public.incidents;
create policy "Municipal users read assigned incidents" on public.incidents for select
  using (municipality_id is not null and public.is_municipal_member(municipality_id));
drop policy if exists "Municipal users update assigned incidents" on public.incidents;
create policy "Municipal users update assigned incidents" on public.incidents for update
  using (municipality_id is not null and public.is_municipal_member(municipality_id))
  with check (municipality_id is not null and public.is_municipal_member(municipality_id));
drop policy if exists "Incident reporter reads own updates" on public.incident_updates;
create policy "Incident participants read updates" on public.incident_updates for select using (
  (internal=false and exists(select 1 from public.incidents i where i.id=incident_id and i.reporter_id=auth.uid()))
  or exists(select 1 from public.incidents i where i.id=incident_id and public.is_municipal_member(i.municipality_id))
);
drop policy if exists "Municipal users add incident updates" on public.incident_updates;
create policy "Municipal users add incident updates" on public.incident_updates for insert with check (
  author_id=auth.uid() and exists(select 1 from public.incidents i where i.id=incident_id and public.is_municipal_member(i.municipality_id))
);
drop policy if exists "Municipal members read notices" on public.municipal_notices;
create policy "Municipal members read notices" on public.municipal_notices for select
  using (public.is_municipal_member(municipality_id) or public.is_platform_admin());
drop policy if exists "Municipal members create notices" on public.municipal_notices;
create policy "Municipal members create notices" on public.municipal_notices for insert
  with check (author_id=auth.uid() and public.is_municipal_member(municipality_id));
drop policy if exists "Municipal members update notices" on public.municipal_notices;
create policy "Municipal members update notices" on public.municipal_notices for update
  using (public.is_municipal_member(municipality_id) or public.is_platform_admin())
  with check (public.is_municipal_member(municipality_id) or public.is_platform_admin());
drop policy if exists "Public can read published municipal notices" on public.municipal_notices;
create policy "Public can read published municipal notices" on public.municipal_notices for select using (
  status='published' and (ends_at is null or ends_at>now())
);
drop policy if exists "Admins read invitations" on public.municipal_invitations;
create policy "Admins read invitations" on public.municipal_invitations for select using(public.is_platform_admin());
drop policy if exists "Admins manage invitations" on public.municipal_invitations;
create policy "Admins manage invitations" on public.municipal_invitations for all
  using(public.is_platform_admin()) with check(public.is_platform_admin());
drop policy if exists "Municipal members read assignments" on public.incident_assignments;
create policy "Municipal members read assignments" on public.incident_assignments for select
  using(exists(select 1 from public.incidents i where i.id=incident_id and public.is_municipal_member(i.municipality_id)));
drop policy if exists "Admins read audit log" on public.admin_audit_logs;
create policy "Admins read audit log" on public.admin_audit_logs for select using(public.is_platform_admin());
drop policy if exists "Service appends audit log" on public.admin_audit_logs;
create policy "Service appends audit log" on public.admin_audit_logs for insert
  with check(coalesce(auth.role(),'')='service_role');
revoke update, delete, truncate on public.admin_audit_logs from anon, authenticated;
drop policy if exists "Admins manage settings" on public.platform_settings;
create policy "Admins manage settings" on public.platform_settings for all
  using(public.is_platform_admin()) with check(public.is_platform_admin());
-- Contact rows are intentionally service-only; public submissions go through FastAPI.
drop policy if exists "Service only contact enquiries" on public.contact_enquiries;
create policy "Service only contact enquiries" on public.contact_enquiries for all using(false) with check(false);

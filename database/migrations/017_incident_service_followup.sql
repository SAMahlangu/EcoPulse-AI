-- Explicit municipal service commitments and resident follow-up. No deadlines
-- are fabricated for historical reports. Apply after migration 008.
begin;
alter table public.incidents
 add column if not exists response_due_at timestamptz,
 add column if not exists repair_due_at timestamptz,
 add column if not exists first_response_at timestamptz,
 add column if not exists resolved_at timestamptz,
 add column if not exists reopen_count integer not null default 0;
alter table public.incidents drop constraint if exists incident_deadline_order;
alter table public.incidents add constraint incident_deadline_order
 check(response_due_at is null or repair_due_at is null or repair_due_at >= response_due_at);
alter table public.incidents drop constraint if exists incident_reopen_count_valid;
alter table public.incidents add constraint incident_reopen_count_valid check(reopen_count>=0);

update public.incidents i set first_response_at=(
 select min(u.created_at) from public.incident_updates u where u.incident_id=i.id
 and u.official and not u.internal and u.status is not null and u.status<>'submitted')
 where i.first_response_at is null;
update public.incidents i set resolved_at=(
 select max(u.created_at) from public.incident_updates u where u.incident_id=i.id
 and u.official and not u.internal and u.status in ('resolved','closed'))
 where i.status in ('resolved','closed') and i.resolved_at is null;

-- Covers existing status and technician-assignment endpoints consistently.
create or replace function public.track_incident_service_times() returns trigger
 language plpgsql set search_path=public as $$
begin
 if new.status is distinct from old.status then
  if old.status not in ('resolved','closed','duplicate','rejected','unable_to_verify')
    and new.status<>'submitted' then
   new.first_response_at=coalesce(new.first_response_at,now());
  end if;
  if new.status in ('resolved','closed') then
   new.resolved_at=coalesce(new.resolved_at,now());
  else new.resolved_at=null;
  end if;
 end if;
 return new;
end $$;
drop trigger if exists incident_service_times on public.incidents;
create trigger incident_service_times before update of status on public.incidents
 for each row execute function public.track_incident_service_times();

create index if not exists incidents_response_overdue_idx
 on public.incidents(municipality_id,response_due_at)
 where first_response_at is null and status not in ('resolved','closed','duplicate','rejected','unable_to_verify');
create index if not exists incidents_repair_overdue_idx
 on public.incidents(municipality_id,repair_due_at)
 where status not in ('resolved','closed','duplicate','rejected','unable_to_verify');
comment on column public.incidents.response_due_at is 'Explicit municipal commitment, not an automatically inferred service level.';
comment on column public.incidents.repair_due_at is 'Explicit municipal repair commitment; cleared on resident reopening and retained in audit history.';
commit;

-- Reuses incidents, incident_updates, incident_assignments and private incident-media.
begin;
-- The connected database has category types in its existing canonical source view.
-- Preserve UUIDs and backfill only missing types using matching demarcation codes.
update public.municipalities m set municipality_type=s.municipality_type
 from public.municipality_dropdown_source s where m.code=s.code and m.municipality_type is null;
alter table public.incidents drop constraint if exists incidents_status_check;
alter table public.incidents add constraint incidents_status_check check(status in
 ('submitted','acknowledged','under_review','assigned','verified','in_progress','resolved','closed','duplicate','rejected','unable_to_verify'));
create table public.municipal_departments (
 id uuid primary key default gen_random_uuid(),
 municipality_id uuid not null references public.municipalities(id),
 department_name text not null, department_code text not null,
 is_active boolean not null default true, is_triage boolean not null default false,
 created_at timestamptz not null default now(), unique(municipality_id,department_code), unique(id,municipality_id)
);
create unique index department_triage_unique on public.municipal_departments(municipality_id) where is_triage;
create table public.department_memberships (
 user_id uuid not null references public.profiles(id),
 department_id uuid not null references public.municipal_departments(id),
 role text not null default 'employee' check(role in ('employee','manager')),
 is_active boolean not null default true, created_at timestamptz not null default now(),
 primary key(user_id,department_id)
);
create table public.department_routing (
 municipality_id uuid not null references public.municipalities(id),
 category text not null check(category in ('water','electricity','roads','waste','stormwater','environmental','other')),
 department_id uuid not null,
 primary key(municipality_id,category),
 foreign key(department_id,municipality_id) references public.municipal_departments(id,municipality_id)
);
alter table public.municipal_memberships add column can_manage_all_departments boolean not null default false;
alter table public.incidents add column department_id uuid references public.municipal_departments(id);
alter table public.incident_media add column media_kind text not null default 'original'
 check(media_kind in ('original','annotated','repair'));
alter table public.incidents add constraint incident_department_municipality foreign key(department_id,municipality_id)
 references public.municipal_departments(id,municipality_id);
create index incidents_department_status_idx on public.incidents(department_id,status,created_at desc);
create table public.department_assignment_history (
 id uuid primary key default gen_random_uuid(), incident_id uuid not null references public.incidents(id),
 department_id uuid not null references public.municipal_departments(id), assigned_by uuid references public.profiles(id),
 notes text not null, created_at timestamptz not null default now()
);

-- Each municipality gets a real triage queue; no employees are granted access automatically.
insert into public.municipal_departments(municipality_id,department_name,department_code,is_triage)
 select id,'Municipal triage','TRIAGE',true from public.municipalities;

create function public.route_infrastructure_report() returns trigger language plpgsql security definer set search_path=public as $$
begin
 if new.municipality_id is null then raise exception 'A confirmed municipality is required'; end if;
 if TG_OP='INSERT' or new.category is distinct from old.category or new.municipality_id is distinct from old.municipality_id then
  select d.id into new.department_id from department_routing r join municipal_departments d on d.id=r.department_id
   where r.municipality_id=new.municipality_id and r.category=new.category and d.is_active;
  if new.department_id is null then
   insert into municipal_departments(municipality_id,department_name,department_code,is_triage)
    values(new.municipality_id,'Municipal triage','TRIAGE',true) on conflict(municipality_id,department_code) do nothing;
   select id into new.department_id from municipal_departments where municipality_id=new.municipality_id and is_triage and is_active;
  end if;
  if new.department_id is null then raise exception 'No active routing or triage queue'; end if;
 end if;
 return new;
end $$;
create trigger route_infrastructure before insert or update of category,municipality_id on public.incidents
 for each row execute function public.route_infrastructure_report();
create function public.audit_department_assignment() returns trigger language plpgsql security definer set search_path=public as $$
begin
 if TG_OP='INSERT' or new.department_id is distinct from old.department_id then
  insert into department_assignment_history(incident_id,department_id,assigned_by,notes)
  values(new.id,new.department_id,auth.uid(),'Department routing updated');
 end if;
 return new;
end $$;
create trigger audit_infrastructure_assignment after insert or update of department_id on public.incidents
 for each row when(new.department_id is not null) execute function public.audit_department_assignment();
update public.incidents i set department_id=d.id from public.municipal_departments d
 where d.municipality_id=i.municipality_id and d.is_triage and i.department_id is null;

create function public.can_manage_incident(target uuid, actor uuid default auth.uid()) returns boolean
language sql stable security definer set search_path=public as $$
 select exists(select 1 from incidents i join profiles p on p.id=actor
 join municipal_memberships mm on mm.user_id=p.id and mm.municipality_id=i.municipality_id and mm.status='active'
 where i.id=target and p.role='municipal' and p.account_status='active' and
 (mm.can_manage_all_departments or exists(select 1 from department_memberships dm
 join municipal_departments d on d.id=dm.department_id and d.is_active
 where dm.user_id=actor and dm.department_id=i.department_id and dm.is_active)))
$$;
revoke all on function public.can_manage_incident(uuid,uuid) from public;
grant execute on function public.can_manage_incident(uuid,uuid) to authenticated,service_role;
create function public.authorized_incidents(actor uuid) returns setof public.incidents
language sql stable security definer set search_path=public as $$
 select * from incidents where public.can_manage_incident(id,actor)
$$;
revoke all on function public.authorized_incidents(uuid) from public,anon,authenticated;
grant execute on function public.authorized_incidents(uuid) to service_role;
drop policy if exists "Municipal users read assigned incidents" on public.incidents;
drop policy if exists "Municipal users update assigned incidents" on public.incidents;
create policy "Department members read incidents" on public.incidents for select to authenticated
 using(public.can_manage_incident(id));
-- Workflow mutations go through FastAPI, which validates scope and writes audit history.
revoke update,delete on public.incidents from authenticated;
drop policy if exists "Incident participants read updates" on public.incident_updates;
create policy "Scoped incident history" on public.incident_updates for select to authenticated using(
 (not internal and exists(select 1 from public.incidents i where i.id=incident_id and i.reporter_id=auth.uid()))
 or public.can_manage_incident(incident_id));
drop policy if exists "Municipal users add incident updates" on public.incident_updates;
create policy "Department members add updates" on public.incident_updates for insert to authenticated
 with check(author_id=auth.uid() and public.can_manage_incident(incident_id));
drop policy if exists "Municipal users read assigned incident media metadata" on public.incident_media;
create policy "Department members read media" on public.incident_media for select to authenticated
 using(public.can_manage_incident(incident_id));
drop policy if exists "Municipal users read assigned incident media objects" on storage.objects;
create policy "Department members read private objects" on storage.objects for select to authenticated
 using(bucket_id='incident-media' and exists(select 1 from public.incident_media im
 where im.storage_path=name and public.can_manage_incident(im.incident_id)));
drop policy if exists "Municipal members read assignments" on public.incident_assignments;
create policy "Department members read technician assignments" on public.incident_assignments for select to authenticated
 using(public.can_manage_incident(incident_id));
alter table public.municipal_departments enable row level security;
alter table public.department_memberships enable row level security;
alter table public.department_routing enable row level security;
alter table public.department_assignment_history enable row level security;
create policy "Read municipal department directory" on public.municipal_departments for select to authenticated using(true);
create policy "Read own department memberships" on public.department_memberships for select to authenticated using(user_id=auth.uid() or public.is_platform_admin());
create policy "Read routing configuration" on public.department_routing for select to authenticated using(public.is_municipal_member(municipality_id) or public.is_platform_admin());
create policy "Read assignment history" on public.department_assignment_history for select to authenticated using(public.can_manage_incident(incident_id) or exists(select 1 from public.incidents i where i.id=incident_id and i.reporter_id=auth.uid()));
revoke all on public.municipal_departments,public.department_memberships,public.department_routing,public.department_assignment_history from anon,authenticated;
grant select on public.municipal_departments,public.department_memberships,public.department_routing,public.department_assignment_history to authenticated;
commit;

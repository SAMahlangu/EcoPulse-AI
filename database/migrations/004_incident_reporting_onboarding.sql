-- Additive incident reporting, private media, model provenance, and resident onboarding.
alter table public.profiles
  add column if not exists province text,
  add column if not exists suburb text,
  add column if not exists onboarding_completed_at timestamptz;

alter table public.incidents
  add column if not exists public_reference text,
  add column if not exists submission_key uuid,
  add column if not exists ai_suggested_category text,
  add column if not exists resident_confirmed_category text,
  add column if not exists ai_model_name text,
  add column if not exists ai_model_version text,
  add column if not exists municipal_verified_category text,
  add column if not exists municipal_verified_by uuid references public.profiles(id),
  add column if not exists municipal_verified_at timestamptz,
  add column if not exists training_consent boolean not null default false;

update public.incidents set public_reference='EP-'||upper(substr(replace(id::text,'-',''),1,12))
where public_reference is null;
alter table public.incidents alter column public_reference set not null;
create unique index if not exists incidents_public_reference_uidx on public.incidents(public_reference);
create unique index if not exists incidents_resident_submission_key_uidx on public.incidents(reporter_id,submission_key) where submission_key is not null;
create index if not exists incidents_created_status_idx on public.incidents(created_at desc,status);
create index if not exists incidents_municipality_created_idx on public.incidents(municipality_id,created_at desc);

create table if not exists public.incident_ai_analyses (
  id uuid primary key default gen_random_uuid(),
  requester_id uuid not null references public.profiles(id) on delete cascade,
  incident_id uuid references public.incidents(id) on delete set null,
  model_name text not null, model_version text not null,
  available boolean not null, suggested_category text,
  confidence numeric(5,4) check(confidence between 0 and 1),
  observations jsonb not null default '[]', result_metadata jsonb not null default '{}',
  created_at timestamptz not null default now()
);
create index if not exists incident_ai_requester_created_idx on public.incident_ai_analyses(requester_id,created_at desc);
create index if not exists incident_ai_incident_idx on public.incident_ai_analyses(incident_id) where incident_id is not null;
alter table public.incident_ai_analyses enable row level security;
drop policy if exists "Residents read own AI analyses" on public.incident_ai_analyses;
create policy "Residents read own AI analyses" on public.incident_ai_analyses for select using(requester_id=auth.uid());
revoke all on public.incident_ai_analyses from anon,authenticated;
grant select on public.incident_ai_analyses to authenticated;

-- Private media is uploaded by FastAPI using the server-only service role key.
insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
values('incident-media','incident-media',false,10485760,array['image/jpeg','image/png','image/webp'])
on conflict(id) do update set public=false,file_size_limit=10485760,allowed_mime_types=array['image/jpeg','image/png','image/webp'];

drop policy if exists "Residents read own incident media objects" on storage.objects;
create policy "Residents read own incident media objects" on storage.objects for select to authenticated
using(bucket_id='incident-media' and (storage.foldername(name))[1]=auth.uid()::text);

revoke update on public.profiles from anon,authenticated;
grant update(full_name,household_size,notification_preferences) on public.profiles to authenticated;

drop policy if exists "Municipal users read assigned incident media metadata" on public.incident_media;
create policy "Municipal users read assigned incident media metadata" on public.incident_media for select to authenticated
using(exists(select 1 from public.incidents i where i.id=incident_id and public.is_municipal_member(i.municipality_id)));
drop policy if exists "Municipal users read assigned incident media objects" on storage.objects;
create policy "Municipal users read assigned incident media objects" on storage.objects for select to authenticated
using(bucket_id='incident-media' and exists(
  select 1 from public.incident_media im join public.incidents i on i.id=im.incident_id
  where im.storage_path=name and public.is_municipal_member(i.municipality_id)
));

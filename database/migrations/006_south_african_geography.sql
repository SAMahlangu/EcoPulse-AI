-- South African geographic reference data. Apply after migrations 001-005.
-- Municipality rows are seeded separately from the MDB 2026 snapshot using
-- database/seed_sa_geography.py. Existing municipality UUIDs and references
-- are preserved; new reference municipalities start as not onboarded.

create table if not exists public.provinces (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  code text not null unique,
  created_at timestamptz not null default now(),
  constraint provinces_name_unique unique (name)
);

insert into public.provinces (name, code) values
  ('Eastern Cape', 'EC'),
  ('Free State', 'FS'),
  ('Gauteng', 'GP'),
  ('KwaZulu-Natal', 'KZN'),
  ('Limpopo', 'LP'),
  ('Mpumalanga', 'MP'),
  ('Northern Cape', 'NC'),
  ('North West', 'NW'),
  ('Western Cape', 'WC')
on conflict (code) do update set name = excluded.name;

alter table public.municipalities
  add column if not exists province_id uuid references public.provinces(id),
  add column if not exists municipality_type text,
  add column if not exists parent_municipality_id uuid references public.municipalities(id),
  add column if not exists is_onboarded boolean not null default true,
  add column if not exists reference_source text,
  add column if not exists reference_as_of date,
  add column if not exists reference_verified boolean not null default false,
  add column if not exists reference_active boolean;

alter table public.profiles
  add column if not exists province_id uuid references public.provinces(id);

comment on column public.municipalities.reference_as_of is
  'Effective date of the source reference row; the download date is kept in the bundled seed metadata.';

-- Backfill existing rows where their province text matches a canonical name.
update public.municipalities m
set province_id = p.id
from public.provinces p
where lower(trim(m.province)) = lower(p.name)
  and m.province_id is distinct from p.id;

update public.profiles profile
set province_id = municipality.province_id,
    province = municipality.province
from public.municipalities municipality
where profile.municipality_id = municipality.id
  and municipality.province_id is not null
  and (profile.province_id is distinct from municipality.province_id
       or profile.province is distinct from municipality.province);

update public.profiles profile
set province_id = province.id
from public.provinces province
where profile.municipality_id is null
  and profile.province_id is null
  and profile.province is not null
  and lower(trim(profile.province)) = lower(province.name);

create or replace function public.validate_profile_geography()
returns trigger
language plpgsql
set search_path = public
as $$
declare
  municipality_province_id uuid;
  municipality_province text;
  community_municipality_id uuid;
  province_name text;
begin
  if new.municipality_id is not null then
    select province_id, province
      into municipality_province_id, municipality_province
    from public.municipalities
    where id = new.municipality_id;
    if not found then
      raise exception 'Selected municipality does not exist';
    end if;
    if new.province_id is distinct from municipality_province_id
       or new.province is distinct from municipality_province then
      raise exception 'Selected municipality must match the profile province';
    end if;
    if new.community_id is not null then
      select municipality_id into community_municipality_id
      from public.communities
      where id = new.community_id;
      if not found or community_municipality_id is distinct from new.municipality_id then
        raise exception 'Selected community must belong to the profile municipality';
      end if;
    end if;
  elsif new.community_id is not null then
    raise exception 'A community requires a selected municipality';
  elsif new.province_id is not null then
    select name into province_name from public.provinces where id = new.province_id;
    if not found or new.province is distinct from province_name then
      raise exception 'Selected province reference does not match the profile province';
    end if;
  elsif new.province is not null then
    select id, name into new.province_id, province_name
    from public.provinces
    where lower(name) = lower(trim(new.province));
    if not found then
      raise exception 'Profile province must match a province reference';
    end if;
    new.province := province_name;
  end if;
  return new;
end;
$$;

drop trigger if exists profiles_validate_geography on public.profiles;
create trigger profiles_validate_geography
before insert or update of province, province_id, municipality_id, community_id
on public.profiles
for each row execute function public.validate_profile_geography();

alter table public.municipalities
  drop constraint if exists municipalities_type_check;
alter table public.municipalities
  add constraint municipalities_type_check
  check (municipality_type is null or municipality_type in ('metropolitan', 'district', 'local'));

create index if not exists municipalities_province_reference_idx
  on public.municipalities (province_id, municipality_type, name)
  where municipality_type is not null;
create index if not exists municipalities_parent_idx
  on public.municipalities (parent_municipality_id)
  where parent_municipality_id is not null;
create index if not exists profiles_province_id_idx
  on public.profiles (province_id);

create or replace function public.validate_municipality_hierarchy()
returns trigger
language plpgsql
set search_path = public
as $$
declare
  parent_type text;
  parent_province_id uuid;
begin
  if new.municipality_type in ('metropolitan', 'district') and new.parent_municipality_id is not null then
    raise exception 'Metropolitan and district municipalities cannot have a parent municipality';
  end if;

  if new.municipality_type = 'local' then
    if new.parent_municipality_id is null then
      raise exception 'Local municipalities must reference their district municipality';
    end if;
    if new.parent_municipality_id = new.id then
      raise exception 'A municipality cannot be its own parent';
    end if;
    select municipality_type, province_id
      into parent_type, parent_province_id
    from public.municipalities
    where id = new.parent_municipality_id;
    if not found or parent_type is distinct from 'district' then
      raise exception 'A local municipality parent must be an existing district municipality';
    end if;
    if new.province_id is distinct from parent_province_id then
      raise exception 'A local municipality and its district must belong to the same province';
    end if;
  end if;
  return new;
end;
$$;

drop trigger if exists municipalities_validate_hierarchy on public.municipalities;
create trigger municipalities_validate_hierarchy
before insert or update of municipality_type, parent_municipality_id, province_id
on public.municipalities
for each row execute function public.validate_municipality_hierarchy();

alter table public.provinces enable row level security;
drop policy if exists "Public province reference read" on public.provinces;
create policy "Public province reference read" on public.provinces
  for select using (true);

-- Geographic names and codes are public reference data. Writes are reserved
-- for the trusted database importer/admin process, not browser clients.
revoke insert, update, delete, truncate on public.provinces from anon, authenticated;
grant select on public.provinces to anon, authenticated;
revoke insert, update, delete, truncate on public.municipalities from anon, authenticated;
grant select on public.municipalities to anon, authenticated;

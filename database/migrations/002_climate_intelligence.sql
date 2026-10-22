-- Climate Intelligence persistence structures. Apply through reviewed migrations.
create table if not exists public.climate_locations (
  id uuid primary key default gen_random_uuid(), province text not null,
  municipality text, location_name text not null,
  latitude double precision not null check(latitude between -90 and 90),
  longitude double precision not null check(longitude between -180 and 180),
  point extensions.geography(Point,4326) generated always as (extensions.st_setsrid(extensions.st_makepoint(longitude,latitude),4326)::extensions.geography) stored,
  created_at timestamptz not null default now(), unique(province,municipality,location_name)
);
create index if not exists climate_locations_point_gix on public.climate_locations using gist(point);
create index if not exists climate_locations_region_idx on public.climate_locations(province,municipality);

create table if not exists public.weather_cache (
  id uuid primary key default gen_random_uuid(), location_id uuid not null references public.climate_locations(id) on delete cascade,
  data_type text not null check(data_type in ('current','forecast','history','analytics')),
  source text not null, payload jsonb not null, fetched_at timestamptz not null default now(), expires_at timestamptz not null,
  unique(location_id,data_type,source)
);
create index if not exists weather_cache_expiry_idx on public.weather_cache(expires_at);

create table if not exists public.climate_risk_assessments (
  id uuid primary key default gen_random_uuid(), location_id uuid not null references public.climate_locations(id) on delete cascade,
  risk_type text not null check(risk_type in ('heat','heavy_rain','dry_conditions','strong_wind')),
  risk_level text not null check(risk_level in ('low','moderate','high','severe','unavailable')),
  risk_score smallint check(risk_score between 0 and 100), explanation text not null,
  indicators jsonb not null default '[]', valid_from timestamptz, valid_until timestamptz,
  engine_version text not null, created_at timestamptz not null default now()
);
create index if not exists climate_risk_location_valid_idx on public.climate_risk_assessments(location_id,valid_until desc);
create index if not exists climate_risk_type_level_idx on public.climate_risk_assessments(risk_type,risk_level,created_at desc);

create table if not exists public.climate_advisories (
  id uuid primary key default gen_random_uuid(), location_id uuid references public.climate_locations(id) on delete set null,
  title text not null, advisory_type text not null, severity text not null check(severity in ('low','moderate','high','severe')),
  description text not null, recommendations jsonb not null default '[]', source text not null,
  is_official boolean not null default false, verification_status text not null,
  valid_from timestamptz, valid_until timestamptz, created_at timestamptz not null default now()
);
create index if not exists climate_advisories_valid_idx on public.climate_advisories(is_official,severity,valid_until desc);
create index if not exists climate_advisories_location_idx on public.climate_advisories(location_id,created_at desc);

create table if not exists public.climate_notification_preferences (
  id uuid primary key default gen_random_uuid(), user_id uuid not null references auth.users(id) on delete cascade,
  location_id uuid references public.climate_locations(id) on delete set null,
  heat_alerts boolean not null default true, rainfall_alerts boolean not null default true,
  wind_alerts boolean not null default true, dry_condition_alerts boolean not null default true,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique(user_id,location_id)
);
create index if not exists climate_preferences_user_idx on public.climate_notification_preferences(user_id);

alter table public.climate_locations enable row level security;
alter table public.weather_cache enable row level security;
alter table public.climate_risk_assessments enable row level security;
alter table public.climate_advisories enable row level security;
alter table public.climate_notification_preferences enable row level security;

create policy "Climate locations are readable" on public.climate_locations for select using (true);
create policy "Weather cache is readable" on public.weather_cache for select using (true);
create policy "Climate risk assessments are readable" on public.climate_risk_assessments for select using (true);
create policy "Climate advisories are readable" on public.climate_advisories for select using (true);
create policy "Residents read own climate preferences" on public.climate_notification_preferences for select using (auth.uid()=user_id);
create policy "Residents create own climate preferences" on public.climate_notification_preferences for insert with check (auth.uid()=user_id);
create policy "Residents update own climate preferences" on public.climate_notification_preferences for update using (auth.uid()=user_id) with check (auth.uid()=user_id);

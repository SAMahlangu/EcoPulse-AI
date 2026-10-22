-- Supabase PostgreSQL foundation. Apply through reviewed Supabase migrations.
create extension if not exists postgis with schema extensions;

create table if not exists public.municipalities (
  id uuid primary key default gen_random_uuid(), province text not null,
  name text not null, code text unique, boundary extensions.geography(MultiPolygon,4326),
  created_at timestamptz not null default now()
);
create table if not exists public.communities (
  id uuid primary key default gen_random_uuid(), municipality_id uuid not null references public.municipalities(id),
  name text not null, centroid extensions.geography(Point,4326), created_at timestamptz not null default now(),
  unique(municipality_id,name)
);
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade, full_name text,
  role text not null default 'resident' check (role in ('resident','municipal','admin')),
  municipality_id uuid references public.municipalities(id), community_id uuid references public.communities(id),
  household_size integer check (household_size between 1 and 30), notification_preferences jsonb not null default '{}',
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table if not exists public.incidents (
  id uuid primary key default gen_random_uuid(), reporter_id uuid not null references public.profiles(id),
  category text not null, title text not null check (length(title) between 3 and 120),
  description text not null check (length(description) between 10 and 2000), location_text text not null,
  location extensions.geography(Point,4326), status text not null default 'submitted' check(status in ('submitted','acknowledged','in_progress','resolved','closed')),
  verified boolean not null default false, created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index if not exists incidents_location_gix on public.incidents using gist(location);
create index if not exists incidents_created_idx on public.incidents(created_at desc);
create index if not exists incidents_reporter_idx on public.incidents(reporter_id,created_at desc);
create table if not exists public.incident_updates (
  id uuid primary key default gen_random_uuid(), incident_id uuid not null references public.incidents(id) on delete cascade,
  author_id uuid references public.profiles(id), status text, message text not null, official boolean not null default false,
  created_at timestamptz not null default now()
);
create table if not exists public.incident_media (
  id uuid primary key default gen_random_uuid(), incident_id uuid not null references public.incidents(id) on delete cascade,
  owner_id uuid not null references public.profiles(id), storage_path text not null, content_type text not null,
  created_at timestamptz not null default now()
);
create table if not exists public.weather_observations (
  id uuid primary key default gen_random_uuid(), location extensions.geography(Point,4326) not null,
  source text not null, observed_at timestamptz not null, payload jsonb not null
);
create index if not exists weather_location_time_gix on public.weather_observations using gist(location);
create index if not exists weather_observed_idx on public.weather_observations(observed_at desc);
create table if not exists public.climate_alerts (
  id uuid primary key default gen_random_uuid(), municipality_id uuid references public.municipalities(id),
  source text not null, title text not null, description text not null, severity text not null,
  starts_at timestamptz, ends_at timestamptz, created_at timestamptz not null default now()
);
create table if not exists public.water_readings (
  id uuid primary key default gen_random_uuid(), resident_id uuid not null references public.profiles(id),
  previous_value numeric(12,3) not null check(previous_value >= 0), current_value numeric(12,3) not null check(current_value >= previous_value),
  previous_date date not null, reading_date date not null check(reading_date > previous_date), household_size integer check(household_size between 1 and 30),
  created_at timestamptz not null default now()
);
create index if not exists water_resident_date_idx on public.water_readings(resident_id,reading_date desc);
create table if not exists public.energy_readings (
  id uuid primary key default gen_random_uuid(), resident_id uuid not null references public.profiles(id),
  previous_value numeric(12,2) not null check(previous_value >= 0), current_value numeric(12,2) not null check(current_value >= previous_value),
  previous_date date not null, reading_date date not null check(reading_date > previous_date), created_at timestamptz not null default now()
);
create index if not exists energy_resident_date_idx on public.energy_readings(resident_id,reading_date desc);
create table if not exists public.appliances (
  id uuid primary key default gen_random_uuid(), resident_id uuid not null references public.profiles(id),
  name text not null, wattage numeric(10,2) check(wattage > 0), hours_per_day numeric(5,2) check(hours_per_day between 0 and 24), created_at timestamptz not null default now()
);
create table if not exists public.community_resources (
  id uuid primary key default gen_random_uuid(), name text not null, category text not null, description text,
  location extensions.geography(Point,4326), address text, contact text, source text not null,
  verified boolean not null default false, updated_at timestamptz not null default now()
);
create index if not exists resources_location_gix on public.community_resources using gist(location);
create table if not exists public.ai_predictions (
  id uuid primary key default gen_random_uuid(), resident_id uuid references public.profiles(id), model_name text not null,
  model_version text not null, prediction_type text not null, input_window jsonb not null,
  output jsonb not null, metrics jsonb, created_at timestamptz not null default now()
);
create table if not exists public.notifications (
  id uuid primary key default gen_random_uuid(), resident_id uuid not null references public.profiles(id),
  title text not null, body text not null, category text not null, official boolean not null default false,
  read_at timestamptz, created_at timestamptz not null default now()
);
create index if not exists notifications_resident_unread_idx on public.notifications(resident_id,created_at desc) where read_at is null;

-- Users can only access their own household records. Never allow client-set role escalation.
alter table public.profiles enable row level security;
alter table public.incidents enable row level security;
alter table public.incident_updates enable row level security;
alter table public.incident_media enable row level security;
alter table public.water_readings enable row level security;
alter table public.energy_readings enable row level security;
alter table public.appliances enable row level security;
alter table public.ai_predictions enable row level security;
alter table public.notifications enable row level security;
alter table public.municipalities enable row level security;
alter table public.communities enable row level security;
alter table public.climate_alerts enable row level security;
alter table public.community_resources enable row level security;
alter table public.weather_observations enable row level security;

create policy "Residents read own profile" on public.profiles for select using (auth.uid()=id);
create policy "Residents update own profile" on public.profiles for update using (auth.uid()=id) with check (auth.uid()=id);
create or replace function public.protect_profile_role() returns trigger language plpgsql as $$
begin
  if new.role is distinct from old.role and coalesce(auth.role(),'') <> 'service_role' then
    raise exception 'Profile role is managed by trusted services';
  end if;
  return new;
end; $$;
create trigger protect_profile_role before update on public.profiles for each row execute function public.protect_profile_role();
create policy "Residents read own incidents" on public.incidents for select using (auth.uid()=reporter_id);
create policy "Residents submit incidents" on public.incidents for insert with check (auth.uid()=reporter_id);
create policy "Residents read own readings" on public.water_readings for select using (auth.uid()=resident_id);
create policy "Residents create own water readings" on public.water_readings for insert with check (auth.uid()=resident_id);
create policy "Residents read own energy readings" on public.energy_readings for select using (auth.uid()=resident_id);
create policy "Residents create own energy readings" on public.energy_readings for insert with check (auth.uid()=resident_id);
create policy "Residents manage own appliances" on public.appliances for all using (auth.uid()=resident_id) with check (auth.uid()=resident_id);
create policy "Residents read own notifications" on public.notifications for select using (auth.uid()=resident_id);
create policy "Residents update own notifications" on public.notifications for update using (auth.uid()=resident_id) with check (auth.uid()=resident_id);
create policy "Residents read own predictions" on public.ai_predictions for select using (auth.uid()=resident_id);
create policy "Public municipal geography" on public.municipalities for select using (true);
create policy "Public community geography" on public.communities for select using (true);
create policy "Public climate alerts" on public.climate_alerts for select using (true);
create policy "Public resources" on public.community_resources for select using (true);
create policy "Public weather observations" on public.weather_observations for select using (true);
create policy "Incident reporter reads own updates" on public.incident_updates for select using (exists(select 1 from public.incidents i where i.id=incident_id and i.reporter_id=auth.uid()));
create policy "Incident reporter reads own media" on public.incident_media for select using (auth.uid()=owner_id);
create policy "Incident reporter adds own media" on public.incident_media for insert with check (auth.uid()=owner_id and exists(select 1 from public.incidents i where i.id=incident_id and i.reporter_id=auth.uid()));

-- Role is assigned only by trusted server-side profile creation, never via user-editable metadata.
create or replace function public.create_resident_profile() returns trigger language plpgsql security definer set search_path=public as $$
begin insert into public.profiles(id,full_name,role) values(new.id, coalesce(new.raw_user_meta_data->>'full_name',''), 'resident'); return new; end; $$;
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users for each row execute procedure public.create_resident_profile();

-- Additive household water intelligence. Legacy public.water_readings stays intact.
-- Apply through the existing Supabase migration process; no demo data is inserted.
begin;

create table public.water_households (
 id uuid primary key default gen_random_uuid(),
 owner_id uuid not null unique references public.profiles(id) on delete cascade,
 name text not null default 'My household' check(length(trim(name)) between 1 and 80),
 household_size integer not null default 4 check(household_size between 1 and 30),
 daily_target_litres numeric(12,3) not null default 600 check(daily_target_litres > 0 and daily_target_litres <= 10000000),
 tariff_per_kl numeric(12,3) not null default 0 check(tariff_per_kl >= 0 and tariff_per_kl <= 100000),
 timezone text not null default 'Africa/Johannesburg',
 created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);

create table public.water_consumption_daily (
 id uuid primary key default gen_random_uuid(),
 household_id uuid not null references public.water_households(id) on delete cascade,
 date date not null,
 litres numeric(12,3) not null check(litres >= 0 and litres <= 10000000),
 target_litres numeric(12,3) not null check(target_litres > 0 and target_litres <= 10000000),
 data_source text not null default 'manual' check(data_source='manual'),
 is_simulation boolean not null default false check(not is_simulation),
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 unique(household_id,date), unique(household_id,id)
);

create table public.water_meter_readings (
 id uuid primary key default gen_random_uuid(),
 household_id uuid not null references public.water_households(id) on delete cascade,
 reading_at timestamptz not null,
 cumulative_kl numeric(16,6) not null check(cumulative_kl >= 0 and cumulative_kl <= 1000000000),
 is_reset boolean not null default false,
 reading_source text not null default 'manual' check(reading_source='manual'),
 notes text not null default '' check(length(notes)<=1000),
 created_at timestamptz not null default now(), unique(household_id,reading_at)
);

create table public.water_consumption_explanations (
 id uuid primary key default gen_random_uuid(),
 household_id uuid not null references public.water_households(id) on delete cascade,
 consumption_id uuid,
 consumption_date date not null,
 reasons text[] not null default '{}' check(cardinality(reasons)<=9),
 text text not null default '' check(length(text)<=2000),
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 unique(household_id,consumption_date),
 foreign key(household_id,consumption_id) references public.water_consumption_daily(household_id,id)
);

create table public.water_ai_insights (
 id uuid primary key default gen_random_uuid(),
 household_id uuid not null references public.water_households(id) on delete cascade,
 consumption_id uuid,
 consumption_date date not null,
 input_hash text not null check(input_hash ~ '^[0-9a-f]{64}$'),
 source text not null check(source in ('gemini','rules')),
 model text not null check(length(model) between 1 and 120),
 analysis jsonb not null check(jsonb_typeof(analysis)='object'),
 generated_at timestamptz not null default now(), expires_at timestamptz not null,
 unique(household_id,consumption_date,input_hash),
 foreign key(household_id,consumption_id) references public.water_consumption_daily(household_id,id)
);
create index water_ai_insights_latest_idx on public.water_ai_insights(household_id,consumption_date,generated_at desc);

create table public.water_alerts (
 id uuid primary key default gen_random_uuid(),
 household_id uuid not null references public.water_households(id) on delete cascade,
 consumption_date date not null,
 alert_type text not null check(length(alert_type) between 1 and 80),
 severity text not null check(severity in ('info','warning','high','critical')),
 message text not null check(length(message) between 1 and 2000),
 status text not null default 'unread' check(status in ('unread','read','dismissed','resolved')),
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 unique(household_id,consumption_date,alert_type)
);
create index water_alerts_history_idx on public.water_alerts(household_id,consumption_date desc,created_at desc);

-- One shared notification per real household/date, even after a correction or retry.
create table public.water_daily_reports (
 id uuid primary key default gen_random_uuid(),
 household_id uuid not null references public.water_households(id) on delete cascade,
 consumption_date date not null,
 report jsonb not null check(jsonb_typeof(report)='object'),
 notification_id uuid unique references public.notifications(id) on delete set null,
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 unique(household_id,consumption_date)
);

-- Simulation values, explanations, analyses and alert states live only in this JSON.
create table public.water_simulation_sessions (
 id uuid primary key default gen_random_uuid(),
 user_id uuid not null references public.profiles(id) on delete cascade,
 scenario text not null check(scenario in ('normal','excessive','possible_leak','weekend','gradual_increase','saving')),
 payload jsonb not null check(jsonb_typeof(payload)='object' and pg_column_size(payload)<=1000000),
 created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index water_simulation_sessions_owner_idx on public.water_simulation_sessions(user_id,created_at desc);

create function public.validate_water_household() returns trigger language plpgsql
set search_path=public,pg_catalog as $$
begin
 if not exists(select 1 from pg_timezone_names where name=new.timezone) then
  raise exception 'Choose a supported IANA timezone';
 end if;
 new.updated_at:=now(); return new;
end $$;
create trigger water_household_valid before insert or update on public.water_households
 for each row execute function public.validate_water_household();

alter table public.water_households enable row level security;
alter table public.water_consumption_daily enable row level security;
alter table public.water_meter_readings enable row level security;
alter table public.water_consumption_explanations enable row level security;
alter table public.water_ai_insights enable row level security;
alter table public.water_alerts enable row level security;
alter table public.water_daily_reports enable row level security;
alter table public.water_simulation_sessions enable row level security;

create policy "Active residents read their water household" on public.water_households for select to authenticated
 using(owner_id=auth.uid() and exists(select 1 from public.profiles p where p.id=auth.uid() and p.role='resident' and coalesce(p.account_status,'active')='active'));

-- API-only writes preserve chronology, AI validation, alert deduplication and real/demo isolation.
do $$
declare table_name text;
begin
 foreach table_name in array array['water_consumption_daily','water_meter_readings','water_consumption_explanations','water_ai_insights','water_alerts','water_daily_reports'] loop
  execute format('create policy "Residents read owned water records" on public.%I for select to authenticated using(exists(select 1 from public.water_households h where h.id=household_id and h.owner_id=auth.uid()))',table_name);
 end loop;
end $$;
create policy "Active residents read their water simulations" on public.water_simulation_sessions for select to authenticated
 using(user_id=auth.uid() and exists(select 1 from public.profiles p where p.id=auth.uid() and p.role='resident' and coalesce(p.account_status,'active')='active'));

revoke all on public.water_households,public.water_consumption_daily,public.water_meter_readings,
 public.water_consumption_explanations,public.water_ai_insights,public.water_alerts,
 public.water_daily_reports,public.water_simulation_sessions from anon,authenticated;
grant select on public.water_households,public.water_consumption_daily,public.water_meter_readings,
 public.water_consumption_explanations,public.water_ai_insights,public.water_alerts,
 public.water_daily_reports,public.water_simulation_sessions to authenticated;
grant all on public.water_households,public.water_consumption_daily,public.water_meter_readings,
 public.water_consumption_explanations,public.water_ai_insights,public.water_alerts,
 public.water_daily_reports,public.water_simulation_sessions to service_role;

comment on table public.water_consumption_daily is 'Actual manually reported daily litres only. Legacy meter periods remain in water_readings; meter interval estimates are calculated, not stored as measured daily use.';
comment on table public.water_simulation_sessions is 'Isolated demo records. Never creates real water readings, notifications, incidents or maintenance dispatches.';
comment on table public.water_daily_reports is 'Idempotency ledger for existing in-app notifications. Generated on data/dashboard refresh; no scheduler is installed.';
commit;

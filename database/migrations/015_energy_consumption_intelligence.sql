-- Additive household energy intelligence. Legacy public.energy_readings stays intact.
-- Apply through the existing Supabase migration process; no demo data is inserted.
begin;

create table public.energy_households (
 id uuid primary key default gen_random_uuid(),
 owner_id uuid not null unique references public.profiles(id) on delete cascade,
 name text not null default 'My household' check(length(trim(name)) between 1 and 80),
 household_size integer not null default 4 check(household_size between 1 and 30),
 daily_target_kwh numeric(12,3) not null default 15 check(daily_target_kwh > 0 and daily_target_kwh <= 10000000),
 tariff_per_kwh numeric(12,3) not null default 0 check(tariff_per_kwh >= 0 and tariff_per_kwh <= 100000),
 timezone text not null default 'Africa/Johannesburg',
 created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);

create table public.energy_consumption_daily (
 id uuid primary key default gen_random_uuid(),
 household_id uuid not null references public.energy_households(id) on delete cascade,
 date date not null,
 kwh numeric(12,3) not null check(kwh >= 0 and kwh <= 10000000),
 target_kwh numeric(12,3) not null check(target_kwh > 0 and target_kwh <= 10000000),
 data_source text not null default 'manual' check(data_source='manual'),
 is_simulation boolean not null default false check(not is_simulation),
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 unique(household_id,date), unique(household_id,id)
);

create table public.energy_meter_readings (
 id uuid primary key default gen_random_uuid(),
 household_id uuid not null references public.energy_households(id) on delete cascade,
 reading_at timestamptz not null,
 cumulative_kwh numeric(16,6) not null check(cumulative_kwh >= 0 and cumulative_kwh <= 1000000000),
 is_reset boolean not null default false,
 reading_source text not null default 'manual' check(reading_source='manual'),
 notes text not null default '' check(length(notes)<=1000),
 created_at timestamptz not null default now(), unique(household_id,reading_at)
);

create table public.energy_consumption_explanations (
 id uuid primary key default gen_random_uuid(),
 household_id uuid not null references public.energy_households(id) on delete cascade,
 consumption_id uuid,
 consumption_date date not null,
 reasons text[] not null default '{}' check(cardinality(reasons)<=10 and reasons <@ array['geyser_usage','electric_heating','air_conditioning','laundry','cooking','visitors','working_from_home','charging_devices','additional_appliances','other']::text[]),
 text text not null default '' check(length(text)<=2000),
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 unique(household_id,consumption_date),
 foreign key(household_id,consumption_id) references public.energy_consumption_daily(household_id,id)
);

create table public.energy_ai_insights (
 id uuid primary key default gen_random_uuid(),
 household_id uuid not null references public.energy_households(id) on delete cascade,
 consumption_id uuid,
 consumption_date date not null,
 input_hash text not null check(input_hash ~ '^[0-9a-f]{64}$'),
 source text not null check(source in ('gemini','rules')),
 model text not null check(length(model) between 1 and 120),
 analysis jsonb not null check(jsonb_typeof(analysis)='object'),
 generated_at timestamptz not null default now(), expires_at timestamptz not null,
 unique(household_id,consumption_date,input_hash),
 foreign key(household_id,consumption_id) references public.energy_consumption_daily(household_id,id)
);
create index energy_ai_insights_latest_idx on public.energy_ai_insights(household_id,consumption_date,generated_at desc);

create table public.energy_alerts (
 id uuid primary key default gen_random_uuid(),
 household_id uuid not null references public.energy_households(id) on delete cascade,
 consumption_date date not null,
 alert_type text not null check(length(alert_type) between 1 and 80),
 severity text not null check(severity in ('info','warning','high','critical')),
 message text not null check(length(message) between 1 and 2000),
 status text not null default 'unread' check(status in ('unread','read','dismissed','resolved')),
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 unique(household_id,consumption_date,alert_type)
);
create index energy_alerts_history_idx on public.energy_alerts(household_id,consumption_date desc,created_at desc);

-- One shared notification per real household/date, even after a correction or retry.
create table public.energy_daily_reports (
 id uuid primary key default gen_random_uuid(),
 household_id uuid not null references public.energy_households(id) on delete cascade,
 consumption_date date not null,
 report jsonb not null check(jsonb_typeof(report)='object'),
 notification_id uuid unique references public.notifications(id) on delete set null,
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 unique(household_id,consumption_date)
);

-- Reuse the existing resident-owned appliance inventory instead of duplicating it.
-- Legacy nullable power/hours remain readable and are labelled incomplete by the API.
alter table public.appliances add column quantity integer not null default 1 check(quantity between 1 and 100);
alter table public.appliances add column duty_cycle numeric(7,6) not null default 1 check(duty_cycle between 0 and 1);
alter table public.appliances add column updated_at timestamptz not null default now();
alter table public.appliances add constraint energy_appliance_valid_input check(
 length(trim(name)) between 1 and 100 and wattage is not null and wattage > 0 and wattage <= 1000000
 and hours_per_day is not null and hours_per_day between 0 and 24) not valid;
create index energy_appliance_owner_idx on public.appliances(resident_id,created_at,id);
-- Restrictive combines with the legacy permissive manage-own policy.
create policy "Only active residents access energy appliance models" on public.appliances
 as restrictive for all to authenticated
 using(resident_id=auth.uid() and exists(select 1 from public.profiles p where p.id=auth.uid() and p.role='resident' and coalesce(p.account_status,'active')='active'))
 with check(resident_id=auth.uid() and exists(select 1 from public.profiles p where p.id=auth.uid() and p.role='resident' and coalesce(p.account_status,'active')='active'));

create table public.energy_prepaid_purchases (
 id uuid primary key default gen_random_uuid(),
 household_id uuid not null references public.energy_households(id) on delete cascade,
 purchase_date timestamptz not null,
 amount_zar numeric(14,4) not null check(amount_zar > 0 and amount_zar <= 10000000),
 units_kwh numeric(14,4) not null check(units_kwh > 0 and units_kwh <= 10000000),
 created_at timestamptz not null default now(),
 unique(household_id,purchase_date,amount_zar,units_kwh)
);
create index energy_prepaid_purchase_owner_date_idx on public.energy_prepaid_purchases(household_id,purchase_date);

create table public.energy_prepaid_balances (
 id uuid primary key default gen_random_uuid(),
 household_id uuid not null references public.energy_households(id) on delete cascade,
 observed_at timestamptz not null,
 remaining_kwh numeric(14,4) not null check(remaining_kwh >= 0 and remaining_kwh <= 10000000),
 created_at timestamptz not null default now(),
 unique(household_id,observed_at)
);
create index energy_prepaid_balance_owner_date_idx on public.energy_prepaid_balances(household_id,observed_at);

-- Simulation values, explanations, analyses and alert states live only in this JSON.
create table public.energy_simulation_sessions (
 id uuid primary key default gen_random_uuid(),
 user_id uuid not null references public.profiles(id) on delete cascade,
 scenario text not null check(scenario in ('normal','excessive','geyser','heater','weekend','gradual_increase','saving')),
 payload jsonb not null check(jsonb_typeof(payload)='object' and pg_column_size(payload)<=1000000),
 created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index energy_simulation_sessions_owner_idx on public.energy_simulation_sessions(user_id,created_at desc);

create function public.validate_energy_household() returns trigger language plpgsql
set search_path=public,pg_catalog as $$
begin
 if not exists(select 1 from pg_timezone_names where name=new.timezone) then
  raise exception 'Choose a supported IANA timezone';
 end if;
 new.updated_at:=now(); return new;
end $$;
create trigger energy_household_valid before insert or update on public.energy_households
 for each row execute function public.validate_energy_household();

alter table public.energy_households enable row level security;
alter table public.energy_consumption_daily enable row level security;
alter table public.energy_meter_readings enable row level security;
alter table public.energy_consumption_explanations enable row level security;
alter table public.energy_ai_insights enable row level security;
alter table public.energy_alerts enable row level security;
alter table public.energy_daily_reports enable row level security;
alter table public.energy_simulation_sessions enable row level security;
alter table public.energy_prepaid_purchases enable row level security;
alter table public.energy_prepaid_balances enable row level security;

create policy "Active residents read their energy household" on public.energy_households for select to authenticated
 using(owner_id=auth.uid() and exists(select 1 from public.profiles p where p.id=auth.uid() and p.role='resident' and coalesce(p.account_status,'active')='active'));

-- API-only writes preserve chronology, AI validation, alert deduplication and real/demo isolation.
do $$
declare table_name text;
begin
 foreach table_name in array array['energy_consumption_daily','energy_meter_readings','energy_consumption_explanations','energy_ai_insights','energy_alerts','energy_daily_reports','energy_prepaid_purchases','energy_prepaid_balances'] loop
  execute format('create policy "Residents read owned energy records" on public.%I for select to authenticated using(exists(select 1 from public.energy_households h where h.id=household_id and h.owner_id=auth.uid()))',table_name);
 end loop;
end $$;
create policy "Active residents read their energy simulations" on public.energy_simulation_sessions for select to authenticated
 using(user_id=auth.uid() and exists(select 1 from public.profiles p where p.id=auth.uid() and p.role='resident' and coalesce(p.account_status,'active')='active'));

revoke all on public.energy_households,public.energy_consumption_daily,public.energy_meter_readings,
 public.energy_consumption_explanations,public.energy_ai_insights,public.energy_alerts,
 public.energy_daily_reports,public.energy_simulation_sessions,public.energy_prepaid_purchases,public.energy_prepaid_balances from anon,authenticated;
grant select on public.energy_households,public.energy_consumption_daily,public.energy_meter_readings,
 public.energy_consumption_explanations,public.energy_ai_insights,public.energy_alerts,
 public.energy_daily_reports,public.energy_simulation_sessions,public.energy_prepaid_purchases,public.energy_prepaid_balances to authenticated;
grant all on public.energy_households,public.energy_consumption_daily,public.energy_meter_readings,
 public.energy_consumption_explanations,public.energy_ai_insights,public.energy_alerts,
 public.energy_daily_reports,public.energy_simulation_sessions,public.energy_prepaid_purchases,public.energy_prepaid_balances to service_role;

comment on table public.energy_consumption_daily is 'Actual manually reported daily kwh only. Legacy meter periods remain in energy_readings; meter interval estimates are calculated, not stored as measured daily use.';
comment on table public.energy_simulation_sessions is 'Isolated demo records. Never creates real energy readings, notifications, incidents or maintenance dispatches.';
comment on table public.energy_daily_reports is 'Idempotency ledger for existing in-app notifications. Generated on data/dashboard refresh; no scheduler is installed.';
commit;

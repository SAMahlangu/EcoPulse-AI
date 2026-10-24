-- EcoMove configuration only. Intentionally run the separate demo seed script.
-- This migration never alters existing incidents, auth, or municipality records.
begin;
create extension if not exists postgis with schema extensions;

create table public.taxi_routes (
 id uuid primary key default gen_random_uuid(),
 name text not null check(length(name) between 3 and 160),
 origin text not null check(length(origin) between 2 and 160),
 destination text not null check(length(destination) between 2 and 160),
 municipality_id uuid not null references public.municipalities(id),
 route_geometry extensions.geography(LineString,4326) not null,
 direction text not null default 'forward' check(direction='forward'),
 description text not null default '' check(length(description)<=2000),
 color text not null default '#0d9488' check(color ~ '^#[0-9A-Fa-f]{6}$'),
 estimated_duration_minutes numeric(8,2) check(estimated_duration_minutes > 0),
 estimated_fare numeric(8,2) check(estimated_fare >= 0),
 data_source text not null check(length(data_source) between 3 and 500),
 is_demo boolean not null default true,
 simulation_mode text not null default 'once' check(simulation_mode in ('once','loop')),
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 unique(municipality_id,name),
 check(extensions.st_npoints(route_geometry::extensions.geometry)>=2),
 check(extensions.st_isvalid(route_geometry::extensions.geometry)),
 check(extensions.st_length(route_geometry)>0),
 check(simulation_mode <> 'loop' or extensions.st_isclosed(route_geometry::extensions.geometry))
);
create index taxi_routes_geometry_gix on public.taxi_routes using gist(route_geometry);
create index taxi_routes_municipality_idx on public.taxi_routes(municipality_id,name);

create table public.taxi_stops (
 id uuid primary key default gen_random_uuid(),
 name text not null check(length(name) between 2 and 160),
 municipality_id uuid not null references public.municipalities(id),
 latitude double precision not null check(latitude between -90 and 90),
 longitude double precision not null check(longitude between -180 and 180),
 location extensions.geography(Point,4326) generated always as
  (extensions.st_setsrid(extensions.st_makepoint(longitude,latitude),4326)::extensions.geography) stored,
 stop_type text not null check(stop_type in ('rank','pickup','dropoff','interchange')),
 is_demo boolean not null default true,
 is_interchange boolean not null default false,
 check(stop_type <> 'interchange' or is_interchange),
 unique(municipality_id,name)
);
create index taxi_stops_location_gix on public.taxi_stops using gist(location);
create index taxi_stops_municipality_idx on public.taxi_stops(municipality_id,name);

create table public.taxi_route_stops (
 id uuid primary key default gen_random_uuid(),
 route_id uuid not null references public.taxi_routes(id) on delete cascade,
 stop_id uuid not null references public.taxi_stops(id),
 stop_sequence integer not null check(stop_sequence>=0),
 estimated_arrival_offset_minutes numeric(8,2) check(estimated_arrival_offset_minutes>=0),
 unique(route_id,stop_sequence), unique(route_id,stop_id)
);
create index taxi_route_stops_stop_idx on public.taxi_route_stops(stop_id,route_id);

create table public.taxi_simulation_vehicles (
 id uuid primary key default gen_random_uuid(),
 vehicle_code text not null unique check(length(vehicle_code) between 3 and 40),
 route_id uuid not null references public.taxi_routes(id) on delete cascade,
 simulation_speed double precision not null default 35 check(simulation_speed between 1 and 120),
 initial_progress double precision not null default 0 check(initial_progress between 0 and 1),
 is_active boolean not null default true
);
create index taxi_simulation_vehicles_route_idx on public.taxi_simulation_vehicles(route_id) where is_active;

-- Stop sequence defines travel direction. A shared interchange uses one stop UUID.
-- Validate the final transaction state so an intentional seed can insert stops in batches.
create function public.validate_ecomove_stops() returns trigger language plpgsql
set search_path=public,extensions as $$
declare affected_route uuid;
begin
 if tg_table_name='taxi_routes' then affected_route:=new.id;
 elsif tg_table_name='taxi_route_stops' then affected_route:=new.route_id;
 else affected_route:=null;
 end if;
 if exists (
  select 1 from public.taxi_route_stops rs
  join public.taxi_routes r on r.id=rs.route_id
  join public.taxi_stops s on s.id=rs.stop_id
  where (affected_route is null and s.id=new.id or rs.route_id=affected_route)
  and (s.municipality_id<>r.municipality_id
       or not extensions.st_dwithin(s.location,r.route_geometry,150))
 ) then raise exception 'Taxi stops must belong to their route municipality and be within 150 metres of its geometry'; end if;
 if exists (
  select 1 from (
   select rs.route_id,rs.stop_sequence,rs.estimated_arrival_offset_minutes as arrival,
    extensions.st_linelocatepoint(r.route_geometry::extensions.geometry,s.location::extensions.geometry) as progress,
    lag(extensions.st_linelocatepoint(r.route_geometry::extensions.geometry,s.location::extensions.geometry)) over w as previous_progress,
    lag(rs.estimated_arrival_offset_minutes) over w as previous_arrival
   from public.taxi_route_stops rs join public.taxi_routes r on r.id=rs.route_id
   join public.taxi_stops s on s.id=rs.stop_id
   where affected_route is null or rs.route_id=affected_route
   window w as (partition by rs.route_id order by rs.stop_sequence)
  ) ordered where progress+0.00001<previous_progress or arrival<previous_arrival
 ) then raise exception 'Taxi stop sequence and arrival offsets must follow the forward route geometry'; end if;
 return null;
end $$;
create constraint trigger taxi_route_stops_valid after insert or update on public.taxi_route_stops
 deferrable initially deferred for each row execute function public.validate_ecomove_stops();
create constraint trigger taxi_route_geometry_valid after update on public.taxi_routes
 deferrable initially deferred for each row execute function public.validate_ecomove_stops();
create constraint trigger taxi_stop_location_valid after update on public.taxi_stops
 deferrable initially deferred for each row execute function public.validate_ecomove_stops();

create function public.touch_ecomove_route() returns trigger language plpgsql as $$
begin new.updated_at:=now(); return new; end $$;
create trigger taxi_route_updated before update on public.taxi_routes
 for each row execute function public.touch_ecomove_route();

alter table public.taxi_routes enable row level security;
alter table public.taxi_stops enable row level security;
alter table public.taxi_route_stops enable row level security;
alter table public.taxi_simulation_vehicles enable row level security;
create policy "Active users read configured routes" on public.taxi_routes for select to authenticated
 using(exists(select 1 from public.profiles where id=auth.uid() and account_status='active'));
create policy "Active users read configured stops" on public.taxi_stops for select to authenticated
 using(exists(select 1 from public.profiles where id=auth.uid() and account_status='active'));
create policy "Active users read route stops" on public.taxi_route_stops for select to authenticated
 using(exists(select 1 from public.profiles where id=auth.uid() and account_status='active'));
create policy "Active users read simulations" on public.taxi_simulation_vehicles for select to authenticated
 using(is_active and exists(select 1 from public.profiles where id=auth.uid() and account_status='active'));
revoke all on public.taxi_routes,public.taxi_stops,public.taxi_route_stops,public.taxi_simulation_vehicles from anon,authenticated;
grant select on public.taxi_routes,public.taxi_stops,public.taxi_route_stops,public.taxi_simulation_vehicles to authenticated;
grant all on public.taxi_routes,public.taxi_stops,public.taxi_route_stops,public.taxi_simulation_vehicles to service_role;
comment on table public.taxi_simulation_vehicles is 'Configuration for browser-only demonstration vehicles. Contains no live GPS positions.';
comment on column public.taxi_stops.is_interchange is 'Only explicitly configured shared stop IDs permit transfers.';
commit;

-- Preserve migration 010 and tighten validation for optional stop arrival offsets.
-- Unknown offsets remain null; later known offsets must not go backwards across gaps.
begin;
create or replace function public.validate_ecomove_stops() returns trigger language plpgsql
set search_path=pg_catalog,public,extensions as $$
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
    max(rs.estimated_arrival_offset_minutes) over (
     partition by rs.route_id order by rs.stop_sequence
     rows between unbounded preceding and 1 preceding
    ) as previous_arrival
   from public.taxi_route_stops rs join public.taxi_routes r on r.id=rs.route_id
   join public.taxi_stops s on s.id=rs.stop_id
   where affected_route is null or rs.route_id=affected_route
   window w as (partition by rs.route_id order by rs.stop_sequence)
  ) ordered where progress+0.00001<previous_progress or arrival<previous_arrival
 ) then raise exception 'Taxi stop sequence and arrival offsets must follow the forward route geometry'; end if;
 return null;
end $$;
commit;

-- Create standard service departments and category routes for local and
-- metropolitan municipalities. Safe to apply more than once: existing
-- departments and routes are preserved.
begin;

with service_departments(department_name, department_code, category) as (
  values
    ('Water and Sanitation', 'WATER', 'water'),
    ('Electricity', 'ELECTRICITY', 'electricity'),
    ('Roads and Transport', 'ROADS', 'roads'),
    ('Waste Management', 'WASTE', 'waste'),
    ('Stormwater', 'STORMWATER', 'stormwater'),
    ('Environmental Management', 'ENVIRONMENTAL', 'environmental')
)
insert into public.municipal_departments(municipality_id, department_name, department_code)
select m.id, sd.department_name, sd.department_code
from public.municipalities m
join service_departments sd on true
where m.is_demonstration is false
  and m.municipality_type in ('local', 'metropolitan')
on conflict (municipality_id, department_code) do nothing;

with service_departments(department_code, category) as (
  values
    ('WATER', 'water'),
    ('ELECTRICITY', 'electricity'),
    ('ROADS', 'roads'),
    ('WASTE', 'waste'),
    ('STORMWATER', 'stormwater'),
    ('ENVIRONMENTAL', 'environmental')
)
insert into public.department_routing(municipality_id, category, department_id)
select d.municipality_id, sd.category, d.id
from public.municipal_departments d
join service_departments sd on sd.department_code = d.department_code
join public.municipalities m on m.id = d.municipality_id
where m.is_demonstration is false
  and m.municipality_type in ('local', 'metropolitan')
  and d.is_active
on conflict (municipality_id, category) do nothing;

-- Keep uncategorised reports in the existing triage queue.
insert into public.department_routing(municipality_id, category, department_id)
select d.municipality_id, 'other', d.id
from public.municipal_departments d
join public.municipalities m on m.id = d.municipality_id
where d.is_triage and d.is_active and m.is_demonstration is false
on conflict (municipality_id, category) do nothing;

commit;

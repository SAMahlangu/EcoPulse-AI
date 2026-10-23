-- Municipal membership includes access to the shared intake queue.
-- Category-routed departments remain restricted to explicitly assigned staff.
begin;

insert into public.department_memberships(user_id, department_id, role, is_active)
select mm.user_id, d.id, 'employee', true
from public.municipal_memberships mm
join public.profiles p on p.id = mm.user_id
join public.municipal_departments d on d.municipality_id = mm.municipality_id
where mm.status = 'active'
  and p.role = 'municipal'
  and coalesce(p.account_status, 'active') = 'active'
  and d.is_triage
  and d.is_active
on conflict (user_id, department_id)
do nothing;

commit;

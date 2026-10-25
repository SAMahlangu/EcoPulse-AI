-- Allow alerts linked to an incident to target every resident in its
-- municipality without requiring configured community rows. Existing alerts
-- keep their community targeting.
begin;

alter table public.service_alerts
  add column if not exists target_scope text not null default 'community';

alter table public.service_alerts
  drop constraint if exists service_alerts_target_scope_check;
alter table public.service_alerts
  add constraint service_alerts_target_scope_check
  check (target_scope in ('community', 'municipality'));

commit;

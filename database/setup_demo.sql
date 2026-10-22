-- Bootstrap a local/demo workspace after migrations 001-004 have been applied.
-- Migrations 001-004 must already be applied. This bootstraps the demo
-- municipality and promotes the existing administrator Auth user.

begin;

do $setup$
declare
  target_admin_id constant uuid := '77c24c20-cf18-4b5e-b50d-4a185b620d15';
  demo_municipality_id uuid;
begin
  if not exists (select 1 from auth.users u where u.id = target_admin_id and lower(u.email) = 'admin@ecopulseai.com') then
    raise exception 'No Supabase Auth user found with the supplied admin UID and email.';
  end if;

  insert into public.municipalities (province, name, code, is_demonstration)
  values ('Gauteng', 'EcoPulse Demo Municipality', 'ECOPULSE-DEMO', true)
  on conflict (code) do update
    set province = excluded.province,
        name = excluded.name,
        is_demonstration = true
  returning id into demo_municipality_id;

  insert into public.communities (municipality_id, name)
  select demo_municipality_id, 'Demo Community'
  where not exists (
    select 1
    from public.communities c
    where c.municipality_id = demo_municipality_id
      and c.name = 'Demo Community'
  );

  -- The SQL Editor runs with a trusted database role; end users must never
  -- be allowed to change their own role through the application.
  insert into public.profiles (id, full_name, role, account_status, updated_at)
  select u.id, coalesce(u.raw_user_meta_data->>'full_name', ''), 'admin', 'active', now()
  from auth.users u
  where u.id = target_admin_id
  on conflict (id) do update
    set role = 'admin', account_status = 'active', updated_at = now();
end;
$setup$;

commit;

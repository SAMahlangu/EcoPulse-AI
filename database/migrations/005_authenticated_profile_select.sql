-- A SELECT RLS policy does not itself grant table access. The authenticated
-- role may query profiles, while RLS still limits each user to auth.uid()=id.
grant select on public.profiles to authenticated;

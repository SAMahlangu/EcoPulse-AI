-- Community resource map data cache and explicit resident issue-sharing consent.
begin;

alter table public.incidents
  add column if not exists community_map_consent boolean not null default false;

comment on column public.incidents.community_map_consent is
  'Resident opt-in to show a category-only, approximately 100m-rounded pin on the community resources map.';

create table if not exists public.community_resource_cache (
  area_key text primary key,
  municipality_id uuid not null references public.municipalities(id) on delete cascade,
  community_id uuid references public.communities(id) on delete set null,
  centre_latitude double precision not null check (centre_latitude between -90 and 90),
  centre_longitude double precision not null check (centre_longitude between -180 and 180),
  payload jsonb not null,
  fetched_at timestamptz not null,
  expires_at timestamptz not null,
  check (expires_at > fetched_at)
);

create index if not exists community_resource_cache_expiry_idx
  on public.community_resource_cache(expires_at);

alter table public.community_resource_cache enable row level security;
revoke all on public.community_resource_cache from anon, authenticated;
grant all on public.community_resource_cache to service_role;

commit;

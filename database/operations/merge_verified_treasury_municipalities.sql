-- Safe, repeatable promotion from the reviewed Treasury staging table.
-- Run only after migrations 006 and 007, then after an administrator has
-- verified rows using a current authoritative source. This never updates or
-- deletes an existing public.municipalities row.

begin;

lock table public.municipalities in share row exclusive mode;

do $preflight$
begin
  if to_regclass('public.municipality_import_review') is null then
    raise exception 'Apply migration 007_treasury_municipality_review.sql first';
  end if;

  if exists (
    select 1 from public.municipality_import_review r
    where r.verification_status = 'verified'
      and r.confirmed_active is true
      and (r.province_id is null or r.category not in ('A', 'B', 'C'))
  ) then
    raise exception 'Some confirmed-active verified rows have no mapped province or supported category';
  end if;

  if exists (
    select 1
    from public.municipality_import_review r
    left join public.municipality_import_review parent
      on parent.code = r.source_parent_code
    where r.verification_status = 'verified'
      and r.confirmed_active is true
      and r.category = 'B'
      and (parent.code is null or parent.category <> 'C'
           or parent.province_id is distinct from r.province_id)
  ) then
    raise exception 'A confirmed-active local municipality has no matching in-province district parent';
  end if;
end;
$preflight$;

-- Insert metropolitan and district references first. Conflicting codes are
-- left untouched so their existing UUIDs and related data remain unchanged.
insert into public.municipalities (
  province, name, code, province_id, municipality_type,
  parent_municipality_id, is_onboarded, is_demonstration,
  reference_source, reference_as_of, reference_verified, reference_active
)
select p.name, r.name, r.code, r.province_id,
       case r.category when 'A' then 'metropolitan' when 'C' then 'district' end,
       null, false, false, 'National Treasury', null, true, true
from public.municipality_import_review r
join public.provinces p on p.id = r.province_id
where r.verification_status = 'verified'
  and r.confirmed_active is true
  and r.category in ('A', 'C')
  and not exists (
    select 1 from public.municipalities existing
    where upper(trim(existing.code)) = upper(trim(r.code))
  )
on conflict (code) do nothing;

-- Insert local municipalities only when their source parent resolves to a
-- district row in the same province. Conflicting municipality codes are
-- skipped and not altered.
insert into public.municipalities (
  province, name, code, province_id, municipality_type,
  parent_municipality_id, is_onboarded, is_demonstration,
  reference_source, reference_as_of, reference_verified, reference_active
)
select p.name, r.name, r.code, r.province_id, 'local', parent.id,
       false, false, 'National Treasury', null, true, true
from public.municipality_import_review r
join public.provinces p on p.id = r.province_id
join public.municipalities parent
  on parent.code = r.source_parent_code
 and parent.municipality_type = 'district'
 and parent.province_id = r.province_id
where r.verification_status = 'verified'
  and r.confirmed_active is true
  and r.category = 'B'
  and not exists (
    select 1 from public.municipalities existing
    where upper(trim(existing.code)) = upper(trim(r.code))
  )
on conflict (code) do nothing;

-- Record successful code links, without changing the linked municipality row.
update public.municipality_import_review r
set promoted_municipality_id = m.id,
    promoted_at = coalesce(r.promoted_at, now()),
    updated_at = now()
from public.municipalities m
where r.verification_status = 'verified'
  and r.confirmed_active is true
  and upper(trim(m.code)) = upper(trim(r.code))
  and m.reference_source = 'National Treasury'
  and m.reference_verified is true
  and m.reference_active is true
  and r.promoted_municipality_id is distinct from m.id;

commit;

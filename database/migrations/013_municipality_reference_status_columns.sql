-- Repair databases where the geography migration was applied before these
-- reference metadata columns were added to migration 006.
begin;

alter table public.municipalities
  add column if not exists reference_source text,
  add column if not exists reference_as_of date,
  add column if not exists reference_verified boolean not null default false,
  add column if not exists reference_active boolean;

comment on column public.municipalities.reference_as_of is
  'Effective date of the source reference row; the download date is kept in the bundled seed metadata.';

commit;

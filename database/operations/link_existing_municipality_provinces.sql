-- Link existing canonical municipality rows to existing provinces.
-- Preserve all municipality UUIDs, existing province links and demo records.
-- Case-insensitive matching covers Kwazulu-Natal / KwaZulu-Natal.
begin;
update public.municipalities m
set province_id=p.id
from public.provinces p
where m.province_id is null
  and m.is_demonstration is false
  and lower(trim(m.province))=lower(trim(p.name));
commit;

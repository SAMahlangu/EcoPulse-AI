import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getJson } from '../../services/api';

export type ProvinceOption = { id: string; name: string; code: string };
export type MunicipalityOption = {
  id: string;
  category: string;
  province_id: string;
  province: string;
  name: string;
  code: string;
  municipality_type: 'metropolitan' | 'district' | 'local';
  parent_municipality_id: string | null;
  parent_municipality: string | null;
  is_onboarded: boolean;
};
export type CommunityOption = { id: string; municipality_id: string; name: string };
type Page<T> = { items: T[]; total: number; limit: number; offset: number };

type SelectProps = {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  required?: boolean;
};

export function ProvinceSelect({ value, onChange, disabled = false, required = true }: SelectProps) {
  const query = useQuery({
    queryKey: ['locations', 'provinces'],
    queryFn: () => getJson<{ items: ProvinceOption[] }>('/locations/provinces'),
    staleTime: 60 * 60 * 1000,
  });

  return <>
    <select required={required} disabled={disabled || query.isPending || query.isError} value={value} onChange={event => onChange(event.target.value)}>
      <option value="">{query.isPending ? 'Loading provinces…' : 'Select a province'}</option>
      {(query.data?.items ?? []).map(province => <option key={province.id} value={province.id}>{province.name}</option>)}
    </select>
    {query.isError && <small className="form-error" role="alert">Could not load provinces. Try again shortly.</small>}
  </>;
}

export function MunicipalitySelect({ provinceId, value, onChange, disabled = false, required = true, infrastructure = false }: SelectProps & { provinceId: string; infrastructure?: boolean }) {
  const [search, setSearch] = useState('');
  const [previousProvince, setPreviousProvince] = useState(provinceId);
  useEffect(() => {
    if (previousProvince !== provinceId) { setSearch(''); onChange(''); setPreviousProvince(provinceId); }
  }, [provinceId, previousProvince, onChange]);
  const query = useQuery({
    queryKey: ['locations', 'municipalities', provinceId, 'canonical-all'],
    queryFn: async () => {
      const items: MunicipalityOption[] = [];
      let page: Page<MunicipalityOption>;
      do {
        page = await getJson<Page<MunicipalityOption>>(`/locations/municipalities?province_id=${encodeURIComponent(provinceId)}&limit=100&offset=${items.length}`);
        items.push(...page.items);
      } while (page.items.length > 0 && items.length < page.total);
      return { ...page, items };
    },
    enabled: Boolean(provinceId),
    staleTime: 0,
    refetchOnMount: 'always',
  });
  const options = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return (query.data?.items ?? []).filter(item => !needle || `${item.name} ${item.code} ${item.category}`.toLocaleLowerCase().includes(needle));
  }, [query.data, search]);

  return <>
    <input type="search" aria-label="Search municipalities" placeholder="Search municipalities" value={search} onChange={event => setSearch(event.target.value)} disabled={!provinceId || disabled || query.isError} />
    <select required={required} disabled={!provinceId || disabled || query.isPending || query.isError} value={value} onChange={event => onChange(event.target.value)}>
      <option value="">{!provinceId ? 'Select a province first' : query.isPending ? 'Loading municipalities…' : 'Select a municipality'}</option>
      {options.filter(municipality=>!infrastructure||municipality.municipality_type==='local'||municipality.municipality_type==='metropolitan').map(municipality => <option key={municipality.id} value={municipality.id}>
        {municipality.name}{infrastructure?` (${municipality.municipality_type})`:''}
      </option>)}
    </select>
    {query.isError && provinceId && <small className="form-error" role="alert">Could not load municipalities. Try again shortly.</small>}
    {query.isSuccess && !query.isFetching && options.length === 0 && <small>No municipalities match this province or search.</small>}
  </>;
}

export function CommunitySelect({ municipalityId, value, onChange, disabled = false, required = false }: SelectProps & { municipalityId: string }) {
  const [search, setSearch] = useState('');
  useEffect(() => setSearch(''), [municipalityId]);
  const query = useQuery({
    queryKey: ['locations', 'communities', municipalityId],
    queryFn: () => getJson<Page<CommunityOption>>(`/locations/communities?municipality_id=${encodeURIComponent(municipalityId)}&limit=100`),
    enabled: Boolean(municipalityId),
    staleTime: 10 * 60 * 1000,
  });
  const options = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return (query.data?.items ?? []).filter(item => !needle || item.name.toLocaleLowerCase().includes(needle));
  }, [query.data, search]);

  return <>
    <input type="search" aria-label="Search communities" placeholder="Search communities" value={search} onChange={event => setSearch(event.target.value)} disabled={!municipalityId || disabled || query.isError} />
    <select required={required} disabled={!municipalityId || disabled || query.isPending || query.isError || options.length === 0} value={value} onChange={event => onChange(event.target.value)}>
      <option value="">{!municipalityId ? 'Select a municipality first' : query.isPending ? 'Loading communities…' : options.length ? 'Select a community (optional)' : 'No communities listed yet'}</option>
      {options.map(community => <option key={community.id} value={community.id}>{community.name}</option>)}
    </select>
    {query.isError && municipalityId && <small className="form-error" role="alert">Could not load communities. Try again shortly.</small>}
  </>;
}

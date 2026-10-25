import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getJson, postJson } from '../services/api';
import { MunicipalitySelect, ProvinceSelect } from './geography/GeographySelects';

type Community = { id: string; name: string; resident_count: number };

export default function CommunityConfiguration() {
  const qc = useQueryClient();
  const [provinceId, setProvinceId] = useState('');
  const [municipalityId, setMunicipalityId] = useState('');
  const [name, setName] = useState('');

  const communities = useQuery({
    queryKey: ['admin-communities', municipalityId],
    queryFn: () => getJson<{ items: Community[] }>(`/admin/communities?municipality_id=${encodeURIComponent(municipalityId)}`),
    enabled: Boolean(municipalityId),
  });
  const create = useMutation({
    mutationFn: () => postJson<Community>('/admin/communities', { municipality_id: municipalityId, name: name.trim() }),
    onSuccess: () => {
      setName('');
      void qc.invalidateQueries({ queryKey: ['admin-communities', municipalityId] });
    },
  });

  return <section className="workspace-panel">
    <div className="workspace-panel-head"><div>
      <h2>Community configuration</h2>
      <p>Add community names to an existing municipality. These records can be used for resident profiles and alert targeting.</p>
    </div></div>
    <div className="notice-fields">
      <label>Province<ProvinceSelect value={provinceId} onChange={id => {
        setProvinceId(id);
        setMunicipalityId('');
      }}/></label>
      <label>Municipality<MunicipalitySelect provinceId={provinceId} value={municipalityId} infrastructure onChange={setMunicipalityId}/></label>
      {municipalityId && <form onSubmit={event => { event.preventDefault(); create.mutate(); }}>
        <label>Community name<input required minLength={2} maxLength={120} value={name} onChange={event => setName(event.target.value)} placeholder="Enter a community name"/></label>
        <button className="button-primary" disabled={name.trim().length < 2 || create.isPending}>{create.isPending ? 'Saving…' : 'Add community'}</button>
      </form>}
    </div>
    {create.isError && <p role="alert" className="form-error">{(create.error as Error).message}</p>}
    {municipalityId && (communities.isPending ? <p>Loading communities…</p> : communities.isError
      ? <p role="alert">{(communities.error as Error).message}</p>
      : communities.data.items.length
        ? <div className="notice-list">{communities.data.items.map(community => <article key={community.id}>
          <b>{community.name}</b><small>{community.resident_count} registered residents</small>
        </article>)}</div>
        : <p>No communities configured for this municipality yet.</p>)}
  </section>;
}

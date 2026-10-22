import { useEffect, useState, type FormEvent } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ArrowRight, Bell, MapPin } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { getJson, putJson } from '../services/api';
import { useAuth } from '../services/auth';
import { CommunitySelect, MunicipalitySelect, ProvinceSelect } from '../components/geography/GeographySelects';

type Setup = { profile: { province_id: string | null; province: string | null; municipality_id: string | null; community_id: string | null; suburb: string | null; notification_preferences: Record<string, boolean> } | null };

export function ResidentOnboarding() {
  const query = useQuery({ queryKey: ['resident-onboarding'], queryFn: () => getJson<Setup>('/resident/onboarding') });
  const { refreshUser } = useAuth(); const navigate = useNavigate();
  const [provinceId, setProvinceId] = useState(''); const [municipalityId, setMunicipalityId] = useState('');
  const [communityId, setCommunityId] = useState(''); const [suburb, setSuburb] = useState('');
  const [preferences, setPreferences] = useState({ incident_updates: true, climate_alerts: true, municipal_notices: true });
  useEffect(() => { const profile = query.data?.profile; if (profile) { setProvinceId(profile.province_id ?? ''); setMunicipalityId(profile.municipality_id ?? ''); setCommunityId(profile.community_id ?? ''); setSuburb(profile.suburb ?? ''); setPreferences({ incident_updates: profile.notification_preferences?.incident_updates ?? true, climate_alerts: profile.notification_preferences?.climate_alerts ?? true, municipal_notices: profile.notification_preferences?.municipal_notices ?? true }); } }, [query.data]);
  const save = useMutation({ mutationFn: () => putJson('/resident/onboarding', { province_id: provinceId, municipality_id: municipalityId, community_id: communityId || null, suburb, notification_preferences: preferences }), onSuccess: async () => { await refreshUser(); navigate('/resident', { replace: true }); } });
  function submit(event: FormEvent) { event.preventDefault(); save.mutate(); }
  if (query.isPending) return <div className="content"><div className="weather-skeleton skeleton"/></div>;
  if (query.isError) return <div className="content"><section className="empty-card"><h2>Community setup is unavailable</h2><p>{(query.error as Error).message}</p><p>Ask your platform administrator to add municipalities and communities, then try again.</p></section></div>;
  return <div className="content onboarding-content"><div className="page-heading"><div><p className="eyebrow">FIRST-TIME SETUP</p><h1>Your community</h1><p className="muted">Choose where you live and the updates you want. You can change these preferences later.</p></div></div><form className="panel onboarding-panel" onSubmit={submit}>
    <div className="onboarding-step"><span>1</span><div><h2>Choose a province</h2><p>Your selection helps us show the right local information.</p></div></div><label>Province<ProvinceSelect value={provinceId} onChange={id => { setProvinceId(id); setMunicipalityId(''); setCommunityId(''); setSuburb(''); }}/></label>
    <div className="onboarding-step"><span>2</span><div><h2>Choose a municipality</h2><p>Search municipalities in your province.</p></div></div><label>Municipality<MunicipalitySelect provinceId={provinceId} value={municipalityId} onChange={id => { setMunicipalityId(id); setCommunityId(''); setSuburb(''); }}/></label>
    <div className="onboarding-step"><span>3</span><div><h2>Add your community or suburb</h2><p>Choose a listed community or enter your suburb if it is not listed.</p></div></div><label>Community (optional)<CommunitySelect municipalityId={municipalityId} value={communityId} onChange={id => setCommunityId(id)}/></label><label>Community or suburb<input required minLength={2} maxLength={120} value={suburb} onChange={e => setSuburb(e.target.value)} placeholder="For example, Hatfield"/><small><MapPin size={13}/> Your exact street address is not needed.</small></label>
    <div className="onboarding-step"><span>4</span><div><h2>Choose notifications</h2><p>Pick the kinds of updates you want to receive.</p></div></div><div className="onboarding-preferences">{([['incident_updates','Report status updates'],['climate_alerts','Climate advisories'],['municipal_notices','Municipal notices']] as const).map(([key,label]) => <label key={key}><span><Bell size={15}/>{label}</span><input type="checkbox" checked={preferences[key]} onChange={e => setPreferences({ ...preferences, [key]: e.target.checked })}/></label>)}</div>
    {save.error && <p className="form-error">{(save.error as Error).message}</p>}<button className="button-primary" disabled={save.isPending || !provinceId || !municipalityId}>{save.isPending ? 'Saving your preferences…' : <>Open resident dashboard <ArrowRight size={16}/></>}</button>
  </form></div>;
}

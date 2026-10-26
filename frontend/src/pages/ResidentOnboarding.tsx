import { useEffect, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, Bell, LocateFixed, Mail, MapPin } from 'lucide-react';
import { useLocation, useNavigate } from 'react-router-dom';
import { getJson, postJson, putJson } from '../services/api';
import { useAuth } from '../services/auth';
import { CommunitySelect, MunicipalitySelect, ProvinceSelect, type CommunityOption } from '../components/geography/GeographySelects';

type Setup = { profile: { province_id: string | null; province: string | null; municipality_id: string | null; community_id: string | null; suburb: string | null; notification_preferences: Record<string, boolean> } | null };
type ResolvedArea = { locality: string | null; province: { id: string; name: string } | null; municipality: { id: string; name: string; province: string } | null; source: string | null; boundary_source: string | null; country_code: string | null };
function normalizeAreaName(value: string) { return value.normalize('NFKC').trim().toLocaleLowerCase('en-ZA'); }

export function ResidentOnboarding() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ['resident-onboarding'], queryFn: () => getJson<Setup>('/resident/onboarding') });
  const { refreshUser } = useAuth(); const navigate = useNavigate(); const location = useLocation();
  const isSettings = location.pathname === '/resident/settings';
  const [provinceId, setProvinceId] = useState(''); const [municipalityId, setMunicipalityId] = useState('');
  const [communityId, setCommunityId] = useState(''); const [suburb, setSuburb] = useState('');
  const [locationBusy, setLocationBusy] = useState(false); const [locationMessage, setLocationMessage] = useState(''); const [locationSource, setLocationSource] = useState('');
  const [preferences, setPreferences] = useState({ incident_updates: true, climate_alerts: true, municipal_notices: true });
  useEffect(() => { const profile = query.data?.profile; if (profile) { setProvinceId(profile.province_id ?? ''); setMunicipalityId(profile.municipality_id ?? ''); setCommunityId(profile.community_id ?? ''); setSuburb(profile.suburb ?? ''); setPreferences({ incident_updates: profile.notification_preferences?.incident_updates ?? true, climate_alerts: profile.notification_preferences?.climate_alerts ?? true, municipal_notices: profile.notification_preferences?.municipal_notices ?? true }); } }, [query.data]);
  const save = useMutation({ mutationFn: () => putJson('/resident/onboarding', { province_id: provinceId, municipality_id: municipalityId, community_id: communityId || null, suburb, notification_preferences: preferences }), onSuccess: async () => { await refreshUser(); await queryClient.invalidateQueries({ queryKey: ['resident-area'] }); navigate('/resident', { replace: true }); } });
  const emailTest = useMutation({ mutationFn: () => postJson<{ status: string; message: string }>('/email/test', {}) });
  function submit(event: FormEvent) { event.preventDefault(); save.mutate(); }
  async function resolveCurrentPosition(latitude: number, longitude: number) {
    setLocationMessage('Looking up your province, municipality, and nearby suburb…');
    try {
      const area = await getJson<ResolvedArea>(`/locations/reverse?latitude=${encodeURIComponent(latitude)}&longitude=${encodeURIComponent(longitude)}`);
      const locality = area.locality?.trim().slice(0, 120) ?? '';
      setProvinceId(area.province?.id ?? '');
      setMunicipalityId(area.municipality?.id ?? '');
      setCommunityId('');
      setSuburb(locality);
      setLocationSource([...new Set([area.source, area.boundary_source].filter(Boolean))].join(' · '));
      if (!area.province?.id || !area.municipality?.id) {
        setLocationMessage(area.country_code && area.country_code.toLowerCase() !== 'za'
          ? 'This location appears to be outside South Africa. Choose your South African area manually below.'
          : area.province?.name
            ? `We found ${area.province.name}, but could not confirm the municipality. Please choose it below.`
            : 'We could not confirm the South African municipality for this location. Choose your area manually below.');
        return;
      }

      // Link a listed community only when its name exactly matches the resolved locality.
      if (locality) {
        try {
          const communityList = await getJson<{ items: CommunityOption[]; total: number; limit: number; offset: number }>(`/locations/communities?municipality_id=${encodeURIComponent(area.municipality.id)}&search=${encodeURIComponent(locality)}&limit=100`);
          const match = communityList.items.find(item => normalizeAreaName(item.name) === normalizeAreaName(locality));
          if (match) setCommunityId(match.id);
        } catch {
          // Keep the editable suburb suggestion if the optional community list is unavailable.
        }
      }

      setLocationMessage(locality
        ? `Suggested ${locality} in ${area.municipality.name}, ${area.province.name}. Review the area before saving.`
        : `Found ${area.municipality.name}, ${area.province.name}. Enter your community or suburb below to finish.`);
    } catch {
      setLocationMessage('We could not look up your current area. Check your connection or enter it manually below.');
    } finally {
      setLocationBusy(false);
    }
  }
  function locateFromDevice() {
    if (!navigator.geolocation) {
      setLocationMessage('This browser cannot share a location. Choose your province and municipality manually below.');
      return;
    }
    setLocationBusy(true); setLocationMessage('Waiting for location permission…'); setLocationSource('');
    navigator.geolocation.getCurrentPosition(
      position => { void resolveCurrentPosition(position.coords.latitude, position.coords.longitude); },
      error => {
        setLocationBusy(false);
        setLocationMessage(error.code === 1
          ? 'Location permission was denied. Allow it in your browser or choose your area manually.'
          : error.code === 3
            ? 'Finding your location took too long. Try again or choose your area manually.'
            : 'Your device could not determine your location. Try again or choose your area manually.');
      },
      { enableHighAccuracy: false, timeout: 15000, maximumAge: 60000 },
    );
  }
  if (query.isPending) return <div className="content"><div className="weather-skeleton skeleton"/></div>;
  if (query.isError) return <div className="content"><section className="empty-card"><h2>Community setup is unavailable</h2><p>{(query.error as Error).message}</p><p>Ask your platform administrator to add municipalities and communities, then try again.</p></section></div>;
  return <div className="content onboarding-content"><div className="page-heading"><div><p className="eyebrow">FIRST-TIME SETUP</p><h1>Your community</h1><p className="muted">Choose where you live and the updates you want. You can change these preferences later.</p></div></div><form className="panel onboarding-panel" onSubmit={submit}>
    <section className="onboarding-location" aria-label="Find your community automatically"><div><b>Fill this in from your current location</b><p>We’ll suggest your province, municipality, and nearby suburb. You can review and change every field before saving.</p></div><button type="button" className="button-outline" disabled={locationBusy} onClick={locateFromDevice}><LocateFixed size={16}/>{locationBusy ? 'Finding your area…' : 'Use my location'}</button>{locationMessage && <p className="onboarding-location-status" role="status" aria-live="polite">{locationMessage}</p>}{locationSource?.includes('OpenStreetMap Nominatim') && <small>Nearby place names © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a>.</small>}{locationSource?.includes('Municipal Demarcation Board') && <small>Municipal boundaries: <a href="https://www.demarcation.org.za/" target="_blank" rel="noreferrer">Municipal Demarcation Board</a>.</small>}<small>Your saved profile keeps the area you choose, not your GPS coordinates.</small></section>
    <div className="onboarding-step"><span>1</span><div><h2>Choose a province</h2><p>Your selection helps us show the right local information.</p></div></div><label>Province<ProvinceSelect value={provinceId} onChange={id => { setProvinceId(id); setMunicipalityId(''); setCommunityId(''); setSuburb(''); }}/></label>
    <div className="onboarding-step"><span>2</span><div><h2>Choose a municipality</h2><p>Search municipalities in your province.</p></div></div><label>Municipality<MunicipalitySelect provinceId={provinceId} value={municipalityId} resetOnProvinceChange={false} onChange={id => { setMunicipalityId(id); setCommunityId(''); setSuburb(''); }}/></label>
    <div className="onboarding-step"><span>3</span><div><h2>Add your community or suburb</h2><p>Choose a listed community or enter your suburb if it is not listed.</p></div></div><label>Community (optional)<CommunitySelect municipalityId={municipalityId} value={communityId} onChange={id => setCommunityId(id)}/></label><label>Community or suburb<input required minLength={2} maxLength={120} value={suburb} onChange={e => setSuburb(e.target.value)} placeholder="For example, Hatfield"/><small><MapPin size={13}/> Your exact street address is not needed.</small></label>
    <div className="onboarding-step"><span>4</span><div><h2>Choose notifications</h2><p>These preferences control the matching in-app alerts and email updates when email delivery is configured.</p></div></div><div className="onboarding-preferences">{([['incident_updates','Report status updates'],['climate_alerts','Climate advisories'],['municipal_notices','Municipal notices and service alerts']] as const).map(([key,label]) => <label key={key}><span><Bell size={15}/>{label}</span><input type="checkbox" checked={preferences[key]} onChange={e => setPreferences({ ...preferences, [key]: e.target.checked })}/></label>)}</div>
    {save.error && <p className="form-error">{(save.error as Error).message}</p>}<button className="button-primary" disabled={save.isPending || locationBusy || !provinceId || !municipalityId}>{save.isPending ? 'Saving your preferences…' : locationBusy ? 'Finding your area…' : <>Open resident dashboard <ArrowRight size={16}/></>}</button>
  </form>{isSettings && <section className="panel email-test-panel"><div><h2><Mail size={18}/> Test email delivery</h2><p>Send one test message to the email address on your signed-in account. Municipal-notice preferences apply; tests are limited to one per minute.</p></div><button className="button-outline" disabled={emailTest.isPending || !preferences.municipal_notices} onClick={() => emailTest.mutate()}>{emailTest.isPending ? 'Sending test…' : 'Send test email'}</button>{!preferences.municipal_notices && <p className="muted">Enable municipal notices above and save your preferences before testing.</p>}{emailTest.data && <p className="email-test-success" role="status">{emailTest.data.message}</p>}{emailTest.isError && <p className="form-error" role="alert">{(emailTest.error as Error).message}</p>}</section>}</div>;
}

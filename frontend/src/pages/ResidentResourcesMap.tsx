import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { CircleMarker, MapContainer, Popup, useMap } from 'react-leaflet';
import { latLngBounds, type LatLngTuple } from 'leaflet';
import { Activity, BookOpen, Building2, Bus, Cross, ExternalLink, Flame, Hospital, Landmark, MapPin, RefreshCw, School, Search, Shield, Users } from 'lucide-react';
import { getJson } from '../services/api';
import { useResidentArea } from '../services/residentArea';
import { Link } from 'react-router-dom';
import StreetMapLayer from '../components/ecomove/EcoMoveTileLayer';
import { CommunityServiceAlerts } from '../components/CommunityServiceAlerts';
import 'leaflet/dist/leaflet.css';
import './resident-resources-map.css';

type ResourceCategory = 'health' | 'pharmacy' | 'police' | 'emergency' | 'education' | 'community' | string;
type Resource = {
  id: string; name: string; category: ResourceCategory; category_label: string; facility_type?: string;
  latitude: number; longitude: number; address?: string | null; contact?: string | null;
  description?: string | null; source: string; source_url?: string | null; verified: boolean;
  ownership?: string | null; municipality?: string | null; province?: string | null;
};
type SharedIssue = { category: string; status: string; latitude: number; longitude: number; reported_on: string };
type ResourceMapData = {
  municipality: string; centre: { latitude: number; longitude: number }; radius_meters: number;
  resources: Resource[]; issues: SharedIssue[];
  sources: { name: string; status: 'available' | 'unavailable' }[];
  fetched_at: string | null; checked_at?: string | null; stale: boolean;
};

const categoryFilters = [
  ['all', 'All places'], ['health', 'Clinics & hospitals'], ['pharmacy', 'Pharmacies'],
  ['police', 'Police stations'], ['emergency', 'Fire & ambulance'],
  ['education', 'Schools & colleges'], ['government', 'Public offices & courts'],
  ['transport', 'Bus & taxi stops'], ['community', 'Community services'],
] as const;
const pinColours: Record<string, string> = {
  health: '#147b50', pharmacy: '#bd7b12', police: '#2868a5', emergency: '#be493b',
  education: '#7754a8', government: '#9a5a25', transport: '#387c92',
  community: '#087e7c', other: '#69766d', issue: '#d97b25',
};

function distanceMetres(a: LatLngTuple, b: LatLngTuple) {
  const radians = (degrees: number) => degrees * Math.PI / 180;
  const dLat = radians(b[0] - a[0]);
  const dLon = radians(b[1] - a[1]);
  const value = Math.sin(dLat / 2) ** 2 + Math.cos(radians(a[0])) * Math.cos(radians(b[0])) * Math.sin(dLon / 2) ** 2;
  return 6_371_000 * 2 * Math.atan2(Math.sqrt(value), Math.sqrt(1 - value));
}

function distanceText(metres: number) {
  return metres < 1000 ? `${Math.round(metres / 10) * 10} m away` : `${(metres / 1000).toFixed(1)} km away`;
}

function MapFit({ centre, points }: { centre: LatLngTuple; points: LatLngTuple[] }) {
  const map = useMap();
  const key = JSON.stringify(points);
  useEffect(() => {
    // Only reframe when the resident changes area or the visible result set changes.
    const bounds = points.length ? latLngBounds(points) : null;
    if (bounds?.isValid()) map.fitBounds(bounds, { padding: [32, 32], maxZoom: 14 });
    else map.setView(centre, 12);
  }, [map, centre[0], centre[1], key]);
  return null;
}

function iconFor(category: string) {
  if (category === 'health') return Hospital;
  if (category === 'pharmacy') return Cross;
  if (category === 'police') return Shield;
  if (category === 'emergency') return Flame;
  if (category === 'education') return School;
  if (category === 'government') return Landmark;
  if (category === 'transport') return Bus;
  if (category === 'community') return BookOpen;
  return Building2;
}

function mapLink(latitude: number, longitude: number) {
  return `https://www.openstreetmap.org/?mlat=${latitude.toFixed(5)}&mlon=${longitude.toFixed(5)}#map=17/${latitude.toFixed(5)}/${longitude.toFixed(5)}`;
}

export default function ResidentResourcesMap() {
  const area = useResidentArea();
  const resourcesQuery = useQuery({
    queryKey: ['resident-community-resources', area.data?.municipality_name, area.data?.suburb],
    queryFn: () => getJson<ResourceMapData>('/resident/community-resources'),
    enabled: !!area.data?.municipality_name,
    staleTime: 15 * 60 * 1000,
    retry: 1,
    refetchOnWindowFocus: false,
  });
  const [category, setCategory] = useState<string>('all');
  const [radiusKm, setRadiusKm] = useState(10);
  const [search, setSearch] = useState('');
  const [showIssues, setShowIssues] = useState(true);
  const data = resourcesQuery.data;
  const centre: LatLngTuple | null = data ? [data.centre.latitude, data.centre.longitude] : null;
  const matching = useMemo(() => {
    if (!data || !centre) return [];
    const term = search.trim().toLocaleLowerCase();
    return data.resources.map(resource => ({ ...resource, distance: distanceMetres(centre, [resource.latitude, resource.longitude]) }))
      .filter(resource => resource.distance <= radiusKm * 1000)
      .filter(resource => category === 'all' || resource.category === category)
      .filter(resource => !term || `${resource.name} ${resource.category_label} ${resource.address ?? ''} ${resource.municipality ?? ''}`.toLocaleLowerCase().includes(term))
      .sort((a, b) => a.distance - b.distance);
  }, [data, centre?.[0], centre?.[1], category, radiusKm, search]);
  const visibleIssues = useMemo(() => {
    if (!data || !centre || !showIssues) return [];
    return data.issues.map(issue => ({ ...issue, distance: distanceMetres(centre, [issue.latitude, issue.longitude]) }))
      .filter(issue => issue.distance <= radiusKm * 1000);
  }, [data, centre?.[0], centre?.[1], radiusKm, showIssues]);
  const mapPoints: LatLngTuple[] = [
    ...matching.slice(0, 600).map(item => [item.latitude, item.longitude] as LatLngTuple),
    ...visibleIssues.map(item => [item.latitude, item.longitude] as LatLngTuple),
  ];
  const availableSources = data?.sources.filter(source => source.status === 'available').length ?? 0;
  const ResourceIcon = iconFor(category === 'all' ? 'community' : category);

  return <div className="content community-resources-page">
    <div className="page-heading community-resources-heading">
      <div><p className="eyebrow">YOUR COMMUNITY</p><h1>Community resources</h1>
        <p className="muted">Find nearby health services, police stations, schools, and other useful places in {data?.municipality ?? area.data?.municipality_name ?? 'your area'}.</p>
      </div>
      <button className="button-outline" type="button" disabled={resourcesQuery.isFetching} onClick={() => void resourcesQuery.refetch()}>
        <RefreshCw size={15}/>{resourcesQuery.isFetching ? 'Updating…' : 'Refresh places'}
      </button>
    </div>

    <CommunityServiceAlerts/>

    <div className="resource-map-notice"><MapPin size={17}/><p><b>South African community map</b><span>Health facilities use the Department of Health map. Other places come from OpenStreetMap contributors. Check current hours and services before travelling.</span></p></div>

    {area.isError && <div className="resource-map-error" role="alert"><div><b>Your saved community could not be loaded.</b><p>{(area.error as Error).message || 'Check your connection and try again.'}</p></div><button className="button-outline" onClick={() => void area.refetch()}>Try again</button></div>}
    {!area.isPending && !area.isError && !area.data?.municipality_name && <section className="resource-area-needed panel"><MapPin size={22}/><h2>Add your community to see nearby places</h2><p>Choose your province and municipality in Profile &amp; settings. The map will then show nearby services for that area.</p><Link className="button-primary" to="/resident/settings">Set up my area</Link></section>}
    {resourcesQuery.isError && <div className="resource-map-error" role="alert"><div><b>We could not load your area map.</b><p>{(resourcesQuery.error as Error).message || 'Check that your municipality is saved, then try again.'}</p></div><button className="button-outline" onClick={() => void resourcesQuery.refetch()}>Try again</button></div>}
    {area.isPending && <div className="resource-map-loading" role="status">Loading your saved community…</div>}
    {!area.isError && !!area.data?.municipality_name && resourcesQuery.isPending && <div className="resource-map-loading" role="status">Loading nearby places for your saved community…</div>}

    {data && <>
      {(data.stale || availableSources < data.sources.length) && <div className="resource-source-warning" role="status">
        {data.stale ? 'Showing saved place data while a map source is unavailable.' : 'Some place sources are unavailable; available sources and saved resources are still shown.'}
      </div>}
      <section className="resource-map-panel panel">
        <div className="resource-controls">
          <label><span>Search places</span><div className="resource-search"><Search size={16}/><input value={search} onChange={event => setSearch(event.target.value)} placeholder="Clinic, police station, school…"/></div></label>
          <label><span>Show places within</span><select value={radiusKm} onChange={event => setRadiusKm(Number(event.target.value))}><option value={5}>5 km</option><option value={10}>10 km</option><option value={20}>20 km</option></select></label>
          <label><span>Place type</span><select value={category} onChange={event => setCategory(event.target.value)}>{categoryFilters.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label className="resource-issue-toggle"><input type="checkbox" checked={showIssues} onChange={event => setShowIssues(event.target.checked)}/><span>Show shared community issues</span></label>
        </div>
        <div className="resource-map-summary"><div><strong><ResourceIcon size={16}/>{matching.length} nearby {matching.length === 1 ? 'place' : 'places'}</strong><span>{visibleIssues.length} anonymous, approximate issue {visibleIssues.length === 1 ? 'pin' : 'pins'} · {radiusKm} km range</span></div>
          <small>{data.checked_at || data.fetched_at ? `Map data refreshed ${new Date(data.checked_at ?? data.fetched_at!).toLocaleString('en-ZA', { timeZone: 'Africa/Johannesburg' })}` : 'Waiting for place data'}</small>
        </div>
        <div className="resource-map-layout">
          <div className="resource-map-canvas">
            <MapContainer center={centre ?? [-29, 25]} zoom={centre ? 12 : 5} scrollWheelZoom={false}>
              <StreetMapLayer/>
              {centre && <MapFit centre={centre} points={mapPoints}/>}
              {matching.slice(0, 600).map(resource => <CircleMarker key={resource.id} center={[resource.latitude, resource.longitude]} radius={resource.verified ? 8 : 7} pathOptions={{ color: '#fff', weight: 2, fillColor: pinColours[resource.category] ?? pinColours.other, fillOpacity: .96 }}>
                <Popup><div className="resource-popup"><b>{resource.name}</b><span>{resource.category_label}{resource.facility_type && resource.facility_type !== resource.category_label ? ` · ${resource.facility_type}` : ''}</span>{resource.address && <small>{resource.address}</small>}{resource.ownership && <small>Ownership: {resource.ownership.replace(/_/g, ' ')}</small>}<small>{resource.source}{resource.verified ? ' · verified source' : ''}</small>{resource.source_url && <a href={resource.source_url} target="_blank" rel="noreferrer">View data source <ExternalLink size={12}/></a>}<a href={mapLink(resource.latitude, resource.longitude)} target="_blank" rel="noreferrer">Open place in OpenStreetMap <ExternalLink size={12}/></a></div></Popup>
              </CircleMarker>)}
              {visibleIssues.map((issue, index) => <CircleMarker key={`${issue.category}-${issue.reported_on}-${index}`} center={[issue.latitude, issue.longitude]} radius={9} pathOptions={{ color: '#fff', weight: 2, fillColor: pinColours.issue, fillOpacity: .96 }}>
                <Popup><div className="resource-popup"><b>{issue.category.replace(/_/g, ' ')} report</b><span>{issue.status.replace(/_/g, ' ')} · reported {new Date(`${issue.reported_on}T12:00:00+02:00`).toLocaleDateString('en-ZA')}</span><small>Shared by a resident as an approximate area pin. Report details and household information stay private.</small></div></Popup>
              </CircleMarker>)}
            </MapContainer>
            <div className="resource-map-legend"><span><i className="legend-health"/>Health</span><span><i className="legend-police"/>Police</span><span><i className="legend-emergency"/>Fire & ambulance</span><span><i className="legend-education"/>Education</span><span><i className="legend-community"/>Community</span><span><i className="legend-issue"/>Reported issue</span></div>
          </div>
          <aside className="resource-results">
            <div className="resource-results-heading"><h2>Nearby places</h2><span>{matching.length}</span></div>
            {!matching.length && <div className="resource-list-empty"><Building2 size={20}/><p>No places match these filters in the selected range.</p><button type="button" onClick={() => { setRadiusKm(20); setCategory('all'); setSearch(''); }}>Show all places within 20 km</button></div>}
            {matching.slice(0, 80).map(resource => {
              const Icon = iconFor(resource.category);
              return <article className="resource-result-row" key={resource.id}>
                <span className="resource-result-icon" style={{ color: pinColours[resource.category] ?? pinColours.other }}><Icon size={16}/></span>
                <div><b>{resource.name}</b><span>{resource.category_label} · {distanceText(resource.distance)}</span>
                  {resource.address && <small>{resource.address}</small>}
                  {resource.ownership && <small>Ownership: {resource.ownership.replace(/_/g, ' ')}</small>}
                  <small>{resource.source}{resource.verified ? ' · verified source' : ''}</small>
                  <a href={mapLink(resource.latitude, resource.longitude)} target="_blank" rel="noreferrer">Open map <ExternalLink size={11}/></a>
                </div>
              </article>;
            })}
            {matching.length > 80 && <p className="resource-list-limit">Showing the 80 closest places. Use search or place type to narrow the list.</p>}
            <div className="resource-issue-summary"><Activity size={15}/><span>{visibleIssues.length ? `${visibleIssues.length} shared issue pins in this range` : 'No residents have chosen to share an active issue pin in this range.'}</span></div>
          </aside>
        </div>
        <div className="resource-map-footer"><span><Users size={14}/> Issue pins are optional, anonymous, and rounded to roughly 100 m. They are resident reports, not confirmed municipal findings.</span>
          <span>{data.sources.map(source => <i className={source.status === 'available' ? 'source-ok' : 'source-down'} key={source.name}>{source.name}: {source.status}</i>)}</span>
        </div>
      </section>
    </>}
  </div>;
}

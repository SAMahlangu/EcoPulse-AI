import { useEffect, useMemo, useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { latLngBounds, type LatLngTuple } from 'leaflet';
import { CircleMarker, MapContainer, Popup, useMap } from 'react-leaflet';
import { MapPin, RefreshCw, ShieldCheck } from 'lucide-react';
import { getJson } from '../services/api';
import { useAuth } from '../services/auth';
import { useResidentArea } from '../services/residentArea';
import { CommunityServiceAlerts } from '../components/CommunityServiceAlerts';
import StreetMapLayer from '../components/ecomove/EcoMoveTileLayer';
import 'leaflet/dist/leaflet.css';
import './resident-map.css';

type Report = { id: string; public_reference: string; title: string; category: string; status: string; location_text: string; latitude: number | null; longitude: number | null; created_at: string };
type MapPage = { items: Report[]; total: number; offset: number; limit: number };
function FitArea({ points, centre }: { points: LatLngTuple[]; centre: LatLngTuple | null }) {
  const map = useMap();
  const pointKey = JSON.stringify(points);
  const centreKey = JSON.stringify(centre);
  useEffect(() => { if (points.length) map.fitBounds(latLngBounds(points), { padding: [35, 35], maxZoom: 14 }); else if (centre) map.setView(centre, 11); }, [map, pointKey, centreKey]);
  return null;
}

export default function ResidentCommunityMap() {
  const { user } = useAuth();
  const area = useResidentArea();
  const [category, setCategory] = useState('');
  const [activeOnly, setActiveOnly] = useState(false);
  const query = useInfiniteQuery({ queryKey: ['resident-community-map', user?.id], initialPageParam: 0, queryFn: ({ pageParam }) => getJson<MapPage>(`/resident/community-map?limit=250&offset=${pageParam}`), getNextPageParam: page => page.offset + page.items.length < page.total && page.items.length ? page.offset + page.items.length : undefined });
  const loaded = query.data?.pages.flatMap(page => page.items) ?? [];
  const reports = loaded.filter(row => (!category || row.category === category) && (!activeOnly || !['resolved', 'closed', 'duplicate', 'rejected', 'unable_to_verify'].includes(row.status)));
  const mapped = reports.filter(row => row.latitude != null && row.longitude != null && Number.isFinite(Number(row.latitude)) && Number.isFinite(Number(row.longitude)));
  const points = useMemo(() => mapped.map(row => [Number(row.latitude), Number(row.longitude)] as LatLngTuple), [JSON.stringify(mapped)]);
  const centre: LatLngTuple | null = area.data?.latitude != null && area.data.longitude != null ? [area.data.latitude, area.data.longitude] : null;
  return <div className="content resident-community">
    <div className="page-heading"><div><p className="eyebrow">YOUR COMMUNITY</p><h1>Community map</h1><p className="muted">Your reports in {area.data?.municipality_name ?? 'your selected area'}, alongside published municipal service updates.</p></div><button className="button-outline" disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCw size={15}/>Refresh reports</button></div>
    <p className="resident-privacy-banner"><ShieldCheck size={18}/>Only your own report locations appear. Other households’ exact locations and attachments stay private.</p>
    <CommunityServiceAlerts/>
    <section className="panel resident-map-panel"><div className="panel-header"><div><h2>Your report locations</h2><p>{query.isPending ? 'Loading your saved report locations...' : query.isError ? (loaded.length ? 'Showing previously loaded locations; current updates are unavailable.' : 'Report location count is unavailable right now.') : `${loaded.length} of ${query.data?.pages[0]?.total ?? 0} saved locations loaded - ${mapped.length} match these filters`}</p></div><Link className="button-primary" to="/resident/report-incident"><MapPin size={16}/>Report an issue</Link></div>
      <div className="resident-map-filters"><label>Category<select value={category} onChange={event => setCategory(event.target.value)}><option value="">All categories</option>{['water', 'electricity', 'roads', 'waste', 'stormwater', 'environmental', 'other'].map(value => <option key={value}>{value}</option>)}</select></label><label className="resident-checkbox"><input type="checkbox" checked={activeOnly} onChange={event => setActiveOnly(event.target.checked)}/>Active reports only</label></div>
      {query.isPending && <p role="status">Loading your report locations...</p>}
      {query.isError && <div role="alert" className="resident-map-data-error"><div><strong>Your report locations are unavailable right now.</strong><p>The map remains available. Please try again when the data service is back.</p>{loaded.length > 0 && <p>Showing {loaded.length} previously loaded locations.</p>}</div><button className="button-outline" disabled={query.isFetching} onClick={() => void query.refetch()}>{query.isFetching ? 'Retrying...' : 'Retry locations'}</button></div>}
      <div className="resident-report-map"><MapContainer center={centre ?? [-29, 25]} zoom={centre ? 11 : 5} scrollWheelZoom={false}><StreetMapLayer/><FitArea points={points} centre={centre}/>{mapped.map(row => <CircleMarker key={row.id} center={[Number(row.latitude), Number(row.longitude)]} radius={8} pathOptions={{ color: ['resolved', 'closed'].includes(row.status) ? '#588671' : '#d88236', fillOpacity: .8 }}><Popup><b>{row.title}</b><p>{row.public_reference}<br/>{row.location_text}<br/>{row.category} | {row.status.replace(/_/g, ' ')}</p><Link to={`/resident/my-reports?report=${encodeURIComponent(row.id)}`}>View your report and updates</Link></Popup></CircleMarker>)}</MapContainer></div>
      {!query.isPending && !query.isError && !mapped.length && <p className="resident-map-empty">No saved locations match this view. Reports without coordinates remain available in <Link to="/resident/my-reports">My reports</Link>.</p>}
      {mapped.length > 0 && <details className="resident-map-list"><summary>View {mapped.length} reports as a list</summary>{mapped.map(row => <article key={row.id}><div><b>{row.title}</b><p>{row.location_text} | {row.status.replace(/_/g, ' ')}</p></div><Link to={`/resident/my-reports?report=${encodeURIComponent(row.id)}`}>View updates</Link></article>)}</details>}
      {!query.isError && query.hasNextPage && <button className="button-outline" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>{query.isFetchingNextPage ? 'Loading...' : 'Load more report locations'}</button>}
    </section>
  </div>;
}

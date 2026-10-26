import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getJson, postJson } from '../services/api';

type Delivery = { id: string; user_id: string; preference: string; status: string; attempts: number; max_attempts: number; next_attempt_at: string; provider: string | null; last_error_code: string | null; accepted_at: string | null; created_at: string; manual_retries: number };
type Deliveries = { items: Delivery[]; total: number; counts: Record<string, number> };
const labels: Record<string, string> = { pending: 'Queued', processing: 'Sending', retry: 'Retry scheduled', accepted: 'Provider accepted', failed: 'Failed', skipped: 'Skipped' };
const kinds: Record<string, string> = { incident_updates: 'Incident update', municipal_notices: 'Municipal notice', climate_alerts: 'Climate alert' };

function when(value: string | null) { return value ? new Date(value).toLocaleString() : '—'; }

export function AdminNotificationDelivery() {
  const [status, setStatus] = useState('');
  const [offset, setOffset] = useState(0);
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ['notification-delivery', status, offset], queryFn: () => getJson<Deliveries>(`/admin/notification-delivery?limit=25&offset=${offset}${status ? `&status=${status}` : ''}`), refetchInterval: 15000 });
  const retry = useMutation({ mutationFn: (id: string) => postJson(`/admin/notification-delivery/${id}/retry`, {}), onSuccess: () => queryClient.invalidateQueries({ queryKey: ['notification-delivery'] }) });
  return <div className="workspace-content">
    <div className="page-heading"><div><p className="eyebrow">NOTIFICATIONS</p><h1>Email delivery</h1><p className="muted">Track queued emails, provider acceptance, and delivery failures.</p></div><button className="button-outline" disabled={query.isFetching} onClick={() => void query.refetch()}>Refresh</button></div>
    <div className="workspace-panel" style={{ padding: 24, marginBottom: 20 }}><p><strong>Provider accepted does not confirm inbox delivery.</strong> Delivery and bounce confirmations are not connected. Check the provider dashboard when investigating an accepted email.</p><p>Retries recheck the resident’s current account status and notification preferences. Message bodies and email addresses are kept out of this view.</p></div>
    <div className="workspace-toolbar"><label>Status<select value={status} onChange={event => { setStatus(event.target.value); setOffset(0); }}><option value="">All statuses</option>{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
    {query.isPending ? <p role="status">Loading email delivery...</p> : query.isError ? <p role="alert">{query.error.message}</p> : <>
      <p aria-live="polite">{query.data.total} matching emails. {Object.entries(query.data.counts).map(([value, count]) => `${labels[value] ?? value}: ${count}`).join(' · ')}</p>
      <div className="workspace-panel"><div className="workspace-table-wrap"><table className="workspace-table"><caption className="sr-only">Notification email delivery jobs</caption><thead><tr><th>Notification</th><th>Resident</th><th>Status</th><th>Attempts</th><th>Latest information</th><th>Action</th></tr></thead><tbody>{query.data.items.map(item => <tr key={item.id}>
        <td><b>{kinds[item.preference] ?? 'Notification'}</b><small>{when(item.created_at)}</small><small title={item.id}>Job {item.id.slice(0, 8)}</small></td><td><span title={item.user_id}>{item.user_id.slice(0, 8)}</span></td>
        <td><span className={`status-pill ${item.status}`}>{labels[item.status] ?? item.status}</span>{item.provider && <small>{item.provider}</small>}</td><td>{item.attempts} / {item.max_attempts}{item.manual_retries > 0 && <small>{item.manual_retries} manual retries</small>}</td>
        <td>{item.status === 'accepted' ? <>Accepted {when(item.accepted_at)}</> : item.status === 'retry' || item.status === 'pending' ? <>Next attempt {when(item.next_attempt_at)}</> : item.last_error_code?.replace(/_/g, ' ') ?? '—'}{item.status === 'retry' && item.last_error_code && <small>{item.last_error_code.replace(/_/g, ' ')}</small>}</td>
        <td>{['failed', 'skipped'].includes(item.status) ? <button type="button" className="button-outline" disabled={retry.isPending} onClick={() => retry.mutate(item.id)}>Retry email</button> : '—'}</td>
      </tr>)}</tbody></table></div>{query.data.items.length === 0 && <p style={{ padding: 24 }}>No email deliveries match this filter.</p>}</div>
      <div className="dialog-actions"><button className="button-outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>Previous</button><span>{offset + (query.data.items.length ? 1 : 0)}–{offset + query.data.items.length} of {query.data.total}</span><button className="button-outline" disabled={offset + 25 >= query.data.total} onClick={() => setOffset(offset + 25)}>Next</button></div>
    </>}
    {retry.error && <p className="form-error" role="alert">{retry.error.message}</p>}{retry.isSuccess && <p role="status">Email queued for retry. Current preferences will be checked before sending.</p>}
  </div>;
}

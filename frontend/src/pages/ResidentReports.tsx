import { useState } from 'react';
import { NavLink, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getJson, postJson } from '../services/api';
import { useAuth } from '../services/auth';
import { AccessibleDialog } from '../components/AccessibleDialog';
import './resident-community.css';

type Report = { id: string; public_reference?: string; title: string; description: string; status: string; category: string; location: string; municipality_name?: string; department_name?: string; created_at: string };
type Followup = { id: string; status: string; response_due_at: string | null; repair_due_at: string | null; first_response_at: string | null; resolved_at: string | null; reopen_count: number; can_reopen: boolean };
const formatTime = (value: string | null) => value ? new Date(value).toLocaleString() : 'Not set';

function ReportDetails({ id, onClose }: { id: string; onClose(): void }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [reason, setReason] = useState('');
  const details = useQuery({ queryKey: ['incidents', 'mine', user?.id, id], refetchInterval: 15_000, queryFn: () => getJson<{ incident: Report; updates: { status: string; message: string; created_at: string }[]; media: { content_type: string; url: string | null }[] }>(`/incidents/${id}`) });
  const followup = useQuery({ queryKey: ['incident-followup', user?.id, id], refetchInterval: 30_000, queryFn: () => getJson<Followup>(`/incidents/${id}/followup`) });
  const reopen = useMutation({ mutationFn: () => postJson<Followup>(`/incidents/${id}/followup`, { message: reason.trim() }), onSuccess: result => { setReason(''); qc.setQueryData(['incident-followup', user?.id, id], result); void qc.invalidateQueries({ queryKey: ['incidents'] }); void qc.invalidateQueries({ queryKey: ['dashboard'] }); void qc.invalidateQueries({ queryKey: ['resident-community-map'] }); void qc.invalidateQueries({ queryKey: ['notifications'] }); } });
  return <AccessibleDialog titleId="resident-report-title" onClose={onClose}>
    <p className="eyebrow">YOUR REPORT AND PUBLIC UPDATES</p><h2 id="resident-report-title">{details.data?.incident.title ?? 'Report details'}</h2>
    {details.isPending ? <p role="status">Loading your report…</p> : details.isError ? <p role="alert">{details.error.message}</p> : <>
      <p>{details.data.incident.public_reference} · {details.data.incident.status.replace(/_/g, ' ')}</p><p>{details.data.incident.description}</p><p>{details.data.incident.municipality_name} / {details.data.incident.department_name ?? 'Municipal triage'}</p>
      {details.data.media.filter(item => item.url).map((item, index) => <img className="municipal-incident-photo" key={index} src={item.url!} alt="Your incident attachment"/>)}
    </>}
    <section className="followup-card" aria-labelledby="resident-deadlines-title"><h3 id="resident-deadlines-title">Service commitments</h3>
      {followup.isPending ? <p role="status">Loading service deadlines…</p> : followup.isError ? <><p role="alert">{followup.error.message}</p><button className="button-outline" onClick={() => void followup.refetch()}>Retry deadlines</button></> : <>
        <dl><div><dt>Response deadline</dt><dd>{formatTime(followup.data.response_due_at)}</dd></div><div><dt>Repair deadline</dt><dd>{formatTime(followup.data.repair_due_at)}</dd></div><div><dt>First official response</dt><dd>{formatTime(followup.data.first_response_at)}</dd></div><div><dt>Recorded resolution</dt><dd>{formatTime(followup.data.resolved_at)}</dd></div></dl>
        <p className="muted">Deadlines are set by the municipality. A missing deadline is not a confirmed repair time.</p>
        {followup.data.can_reopen && <form onSubmit={event => { event.preventDefault(); reopen.mutate(); }}><h3>Is the issue still unresolved?</h3><p>Return this report to municipal review and explain what still needs attention.</p><label>What remains unresolved?<textarea required minLength={10} maxLength={2000} rows={3} value={reason} onChange={event => setReason(event.target.value)} placeholder="Describe what you can still observe at the reported location."/></label><button className="button-primary" disabled={reopen.isPending || reason.trim().length < 10}>{reopen.isPending ? 'Sending follow-up…' : 'Still unresolved — reopen report'}</button></form>}
        {followup.data.reopen_count > 0 && <p className="report-followup-tag">Reopened {followup.data.reopen_count} time{followup.data.reopen_count === 1 ? '' : 's'} · {followup.data.status.replace(/_/g, ' ')}</p>}
      </>}
      {reopen.isError && <p role="alert" className="form-error">{reopen.error.message}</p>}{reopen.isSuccess && <p role="status" className="form-success">Your follow-up was saved. The report is back under review.</p>}
    </section>
    <div className="resident-update-list"><h3>Report history</h3>{details.data?.updates.length ? details.data.updates.map((update, index) => <article key={index}><span>{update.status?.replace(/_/g, ' ') || 'Update'}</span><p>{update.message}</p><small>{new Date(update.created_at).toLocaleString()}</small></article>) : !details.isPending && <p>No public updates have been recorded yet.</p>}</div>
  </AccessibleDialog>;
}

export default function ResidentReports() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const selected = params.get('report');
  const [offset, setOffset] = useState(0);
  const limit = 25;
  const query = useQuery({ queryKey: ['incidents', 'mine', user?.id, offset], refetchInterval: 15_000, queryFn: () => getJson<{ items: Report[]; total?: number }>(`/incidents?limit=${limit}&offset=${offset}`) });
  const setSelected = (id: string | null) => { const next = new URLSearchParams(params); if (id) next.set('report', id); else next.delete('report'); setParams(next, { replace: true }); };
  return <div className="content"><div className="page-heading"><div><p className="eyebrow">YOUR COMMUNITY</p><h1>My reports</h1><p className="muted">Follow public updates, municipal deadlines and unresolved repairs.</p></div></div>
    {query.isPending ? <div className="weather-skeleton skeleton" role="status" aria-label="Loading your reports"/> : query.isError ? <div className="empty-card"><h2>Reports unavailable</h2><p role="alert">{query.error.message}</p><button className="button-outline" onClick={() => void query.refetch()}>Retry reports</button></div> : <>
      {query.data.items.length ? <div className="panel report-list">{query.data.items.map(row => <article key={row.id}><div><b>{row.title}</b><small>{row.public_reference ?? row.id} · {row.municipality_name} · {row.department_name} · {row.category} · {row.location} · {new Date(row.created_at).toLocaleDateString()}</small></div><span className={`status-pill ${row.status}`}>{row.status.replace(/_/g, ' ')}</span><button className="button-outline" onClick={() => setSelected(row.id)}>Updates</button></article>)}</div> : <div className="empty-card"><h2>{offset ? 'No more reports' : 'No reports yet'}</h2><p>{offset ? 'Return to the previous page to review earlier results.' : 'When you submit an incident, it will appear here.'}</p><NavLink className="button-primary" to="/resident/report-incident">Report a local issue</NavLink></div>}
      <div className="incident-page-controls"><span>{query.data.items.length ? `${offset + 1}–${offset + query.data.items.length}` : '0'}{query.data.total != null ? ` of ${query.data.total}` : ''} reports</span><div><button className="button-outline" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - limit))}>Previous</button><button className="button-outline" disabled={query.data.total != null ? offset + limit >= query.data.total : query.data.items.length < limit} onClick={() => setOffset(offset + limit)}>Next</button></div></div>
    </>}
    {selected && <ReportDetails key={selected} id={selected} onClose={() => setSelected(null)}/>}
  </div>;
}

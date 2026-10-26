import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getJson, patchJson } from '../services/api';

type Deadlines = { status: string; response_due_at: string | null; repair_due_at: string | null; first_response_at: string | null; resolved_at: string | null; reopen_count: number };
const localInput = (value: string | null) => { if (!value) return ''; const date = new Date(value); return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}T${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`; };
const readable = (value: string | null) => value ? new Date(value).toLocaleString() : 'Not recorded';

export function IncidentDeadlines({ reportId }: { reportId: string }) {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: ['incident-deadlines', reportId], queryFn: () => getJson<Deadlines>(`/municipal/incidents/${reportId}/deadlines`) });
  const [response, setResponse] = useState('');
  const [repair, setRepair] = useState('');
  const [message, setMessage] = useState('');
  const [editing, setEditing] = useState(false);
  useEffect(() => { if (query.data && !editing) { setResponse(localInput(query.data.response_due_at)); setRepair(localInput(query.data.repair_due_at)); } }, [query.data, editing]);
  const mutation = useMutation({ mutationFn: () => patchJson<Deadlines>(`/municipal/incidents/${reportId}/deadlines`, { response_due_at: response ? new Date(response).toISOString() : null, repair_due_at: repair ? new Date(repair).toISOString() : null, message: message.trim() }), onSuccess: result => { qc.setQueryData(['incident-deadlines', reportId], result); setMessage(''); setEditing(false); void qc.invalidateQueries({ queryKey: ['workspace'] }); void qc.invalidateQueries({ queryKey: ['incident-followup'] }); void qc.invalidateQueries({ queryKey: ['notifications'] }); } });
  const terminal = query.data && ['resolved', 'closed', 'duplicate', 'rejected', 'unable_to_verify'].includes(query.data.status);
  const invalidOrder = response && repair && new Date(repair).getTime() < new Date(response).getTime();
  return <section className="followup-card" aria-labelledby="municipal-deadlines-title"><h3 id="municipal-deadlines-title">Service deadlines and follow-up</h3>
    {query.isPending ? <p role="status">Loading service commitments…</p> : query.isError ? <><p role="alert">{query.error.message}</p><button className="button-outline" onClick={() => void query.refetch()}>Retry deadlines</button></> : <>
      <dl><div><dt>Response due</dt><dd>{readable(query.data.response_due_at)}</dd></div><div><dt>Repair due</dt><dd>{readable(query.data.repair_due_at)}</dd></div><div><dt>First official response</dt><dd>{readable(query.data.first_response_at)}</dd></div><div><dt>Resolved at</dt><dd>{readable(query.data.resolved_at)}</dd></div></dl>
      {query.data.reopen_count > 0 && <p className="report-followup-tag">Resident reopened this report {query.data.reopen_count} time{query.data.reopen_count === 1 ? '' : 's'}. Review their latest public update before setting a new deadline.</p>}
      {!terminal && (editing ? <form onSubmit={event => { event.preventDefault(); mutation.mutate(); }}><div className="deadline-grid"><label>Response due<input type="datetime-local" value={response} onChange={event => setResponse(event.target.value)}/></label><label>Repair due<input type="datetime-local" value={repair} onChange={event => setRepair(event.target.value)}/></label></div><p className="muted">Times use your device’s local timezone. Leave a field empty to clear that commitment.</p><label>Public reason or service update<textarea required minLength={5} maxLength={2000} value={message} onChange={event => setMessage(event.target.value)} rows={3}/></label>{invalidOrder && <p className="form-error" role="alert">Repair must be due at or after the response deadline.</p>}<div className="dialog-actions"><button type="button" className="button-outline" disabled={mutation.isPending} onClick={() => setEditing(false)}>Cancel</button><button className="button-primary" disabled={mutation.isPending || message.trim().length < 5 || Boolean(invalidOrder)}>{mutation.isPending ? 'Saving deadlines…' : 'Save public deadlines'}</button></div></form> : <button className="button-outline" onClick={() => { mutation.reset(); setEditing(true); }}>Set or revise deadlines</button>)}
    </>}
    {mutation.isError && <p role="alert" className="form-error">{mutation.error.message}</p>}{mutation.isSuccess && !editing && <p role="status" className="form-success">Deadlines and the public update were saved together.</p>}
  </section>;
}

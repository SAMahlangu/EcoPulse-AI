import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getJson } from '../services/api';

type Enquiry = { id: string; full_name: string; email: string; organisation: string | null; subject: string; message: string; created_at: string };
export function AdminEnquiries() {
  const [offset, setOffset] = useState(0);
  const query = useQuery({ queryKey: ['admin-enquiries', offset], queryFn: () => getJson<{ items: Enquiry[]; total: number }>(`/admin/enquiries?limit=25&offset=${offset}`), refetchInterval: 30000 });
  return <div className="workspace-content"><div className="page-heading"><div><p className="eyebrow">PUBLIC CONTACT</p><h1>Contact inbox</h1><p className="muted">Messages submitted through the landing page.</p></div><button className="button-outline" disabled={query.isFetching} onClick={() => void query.refetch()}>Refresh</button></div>
    {query.isPending ? <p role="status">Loading enquiries...</p> : query.isError ? <p role="alert">{query.error.message}</p> : <>
      <p>{query.data.total} enquiry submissions</p>
      {query.data.items.length ? query.data.items.map(item => <article className="workspace-panel" key={item.id} style={{ padding: 24, marginBottom: 16 }}><div className="workspace-panel-head"><div><h2>{item.subject}</h2><p>{item.full_name} {item.organisation ? ` / ${item.organisation}` : ''}</p><a href={`mailto:${encodeURIComponent(item.email)}`}>{item.email}</a></div><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString()}</time></div><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', lineHeight: 1.8 }}>{item.message}</p></article>) : <div className="workspace-panel" style={{ padding: 24 }}>No messages yet. Landing-page submissions will appear here.</div>}
      <div className="dialog-actions"><button className="button-outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>Previous</button><button className="button-outline" disabled={offset + 25 >= query.data.total} onClick={() => setOffset(offset + 25)}>Next</button></div>
    </>}
  </div>;
}

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getJson, postJson } from '../services/api';

type Alert = { id:string; service_type:'electricity'|'water'|'transport'; title:string; description:string; severity:string; status:string; target_scope?:'community'|'municipality'; municipality?:string; starts_at:string; estimated_restoration_at:string|null; published_at:string; areas:{id:string;name:string}[]; repair_steps?:{title:string;description:string;status:string;updated_at:string}[]; updates?:{message:string;created_at:string}[] };
type AlertList = {items:Alert[]};

export function CommunityServiceAlerts({service}:{service?:'electricity'|'water'|'transport'}) {
  const [selected,setSelected]=useState<Alert|null>(null);
  const q=useQuery({queryKey:['service-alerts',service],queryFn:()=>getJson<AlertList>(`/service-alerts?limit=50${service?`&service_type=${service}`:''}`),refetchInterval:60_000});
  const items=(q.data?.items??[]).filter(a=>!service||a.service_type===service);
  return <section className="community-alerts panel" aria-labelledby="community-alert-heading">
    <div className="panel-header"><div className="panel-title"><h3 id="community-alert-heading">{service==='electricity'?'Community Electricity Status':service==='water'?'Community Water Supply Status':'Community Service Status'}</h3></div><small>Municipal updates</small></div>
    {q.isPending?<p>Loading community alerts…</p>:q.isError?<p role="alert">{(q.error as Error).message}</p>:items.length?<div className="community-alert-list">{items.map(a=><article key={a.id} className="community-alert-item"><div><b>{a.title}</b><p>{a.service_type} · {a.target_scope==='municipality'?a.municipality:a.areas.map(x=>x.name).join(', ')} · {a.severity} · {a.status.replace('_',' ')}</p><small>Published {new Date(a.published_at).toLocaleString()}{a.estimated_restoration_at?` · Estimated restoration ${new Date(a.estimated_restoration_at).toLocaleString()}`:''}</small></div><button className="button-outline" onClick={()=>setSelected(a)}>View details</button></article>)}</div>:<p className="community-alert-empty">No {service??'municipal service'} alerts have been reported for your municipality. This does not confirm normal service.</p>}
    {selected&&<AlertDialog alert={selected} onClose={()=>setSelected(null)}/>}
  </section>;
}

function AlertDialog({alert,onClose}:{alert:Alert;onClose:()=>void}) {
  const qc=useQueryClient();
  const details=useQuery({queryKey:['service-alert',alert.id],queryFn:()=>getJson<Alert>(`/service-alerts/${alert.id}`)});
  const feedback=useMutation({mutationFn:(response:'yes'|'no'|'not_sure')=>postJson(`/service-alerts/${alert.id}/feedback`,{response}),onSuccess:()=>void qc.invalidateQueries({queryKey:['service-alerts']})});
  const data=details.data;
  return <div className="dialog-scrim" onMouseDown={e=>{if(e.target===e.currentTarget)onClose();}}><section className="incident-dialog" role="dialog" aria-modal="true" aria-labelledby="alert-title"><button className="dialog-close" onClick={onClose} aria-label="Close">×</button><p className="eyebrow">{alert.service_type.toUpperCase()} SERVICE ALERT</p><h2 id="alert-title">{alert.title}</h2><p>{alert.description}</p><p><b>Affected area:</b> {alert.target_scope==='municipality'?alert.municipality:alert.areas.map(x=>x.name).join(', ')}</p><p><b>Status:</b> {alert.status.replace('_',' ')} · <b>Severity:</b> {alert.severity}</p>{alert.estimated_restoration_at&&<p><b>Estimated restoration:</b> {new Date(alert.estimated_restoration_at).toLocaleString()}</p>}{details.isPending?<p>Loading repair progress…</p>:details.isError?<p role="alert">{(details.error as Error).message}</p>:<><h3>Repair progress</h3>{data?.repair_steps?.length?<ol>{data.repair_steps.map((s,i)=><li key={i}><b>{s.title}</b> · {s.status.replace('_',' ')}<p>{s.description}</p><small>Updated {new Date(s.updated_at).toLocaleString()}</small></li>)}</ol>:<p>No public repair stages have been added yet.</p>}{data?.updates?.map((u,i)=><p key={i}>{u.message} <small>{new Date(u.created_at).toLocaleString()}</small></p>)}</>}{alert.status==='restored'&&alert.service_type!=='transport'&&<div><h3>Has your {alert.service_type==='water'?'water supply':'electricity'} been restored?</h3>{(['yes','no','not_sure'] as const).map(v=><button key={v} className="button-outline" disabled={feedback.isPending} onClick={()=>feedback.mutate(v)}>{v==='yes'?'Yes':v==='no'?'No':'Not sure'}</button>)}{feedback.isError&&<p role="alert">{(feedback.error as Error).message}</p>}{feedback.isSuccess&&<p>Response saved. You can update it at any time.</p>}</div>}</section></div>;
}

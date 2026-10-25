import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Lightbulb, Pencil, Plus, Refrigerator, Trash2 } from 'lucide-react';
import { getJson, postJson, putJson } from '../../services/api';
import { getAccessToken } from '../../services/auth';
import type { EnergyAdvisorResponse, EnergyAppliance, EnergyApplianceAnalysis } from '../../types/energy';
import { energyMoney, energyNumber } from '../../types/energy';
import { EnergyDemoBadge } from './EnergyChart';

const choices = ['Electric geyser', 'Refrigerator', 'Television', 'Washing machine', 'Electric heater', 'Air conditioner', 'Microwave', 'Electric stove', 'Kettle', 'Computer', 'Other'];
const blank = { appliance_name: 'Electric heater', power_watts: '', operating_hours: '', quantity: '1', duty_cycle: '100' };
async function deleteAppliance(id: string, query: string) {
  const token = getAccessToken();
  const root = import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api/v1';
  const response = await fetch(`${root}/energy/appliances/${encodeURIComponent(id)}${query}`, { method: 'DELETE', headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(typeof body.detail === 'string' ? body.detail : `Could not remove appliance (${response.status})`); }
}

export function EnergyAppliances({ sessionId }: { sessionId?: string }) {
  const qc = useQueryClient();
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState(blank);
  const [customName, setCustomName] = useState('');
  const appliances = useQuery({ queryKey: ['energy', 'appliances', sessionId ?? 'actual'], queryFn: () => getJson<EnergyApplianceAnalysis>(`/energy/appliances${query}`) });
  const refresh = () => { qc.invalidateQueries({ queryKey: ['energy', 'appliances'] }); qc.invalidateQueries({ queryKey: ['energy', 'advisor'] }); qc.invalidateQueries({ queryKey: ['energy', 'insights'] }); };
  const save = useMutation({ mutationFn: () => {
    const body = { appliance_name: form.appliance_name === 'Other' ? customName : form.appliance_name, power_watts: Number(form.power_watts), operating_hours: Number(form.operating_hours), quantity: Number(form.quantity), duty_cycle: Number(form.duty_cycle) / 100 };
    return editing ? putJson(`/energy/appliances/${encodeURIComponent(editing)}${query}`, body) : postJson(`/energy/appliances${query}`, body);
  }, onSuccess: () => { refresh(); setEditing(null); setForm(blank); setCustomName(''); } });
  const remove = useMutation({ mutationFn: (id: string) => deleteAppliance(id, query), onSuccess: (_, id) => { refresh(); if (editing === id) { setEditing(null); setForm(blank); setCustomName(''); } } });
  const edit = (item: EnergyAppliance) => {
    setEditing(item.id); setForm({ appliance_name: choices.includes(item.appliance_name) ? item.appliance_name : 'Other', power_watts: item.power_watts == null ? '' : String(item.power_watts), operating_hours: item.operating_hours == null ? '' : String(item.operating_hours), quantity: String(item.quantity), duty_cycle: String((item.duty_cycle ?? 1) * 100) }); setCustomName(item.appliance_name);
  };
  const data = appliances.data;
  const pending = save.isPending || remove.isPending;
  return <section className="panel ei-appliances" id="energy-appliances" data-testid="energy-appliances"><div className="ei-panel-heading"><span className="panel-icon energy-icon"><Refrigerator size={18}/></span><div><h3>Appliance Energy Analyser</h3><p>Model appliance use to understand potential contributors.</p></div></div>{sessionId && <EnergyDemoBadge/>}<p className="ei-estimate-label">APPLIANCE ESTIMATES · Separate from metered consumption</p>
    <div className="ei-record-grid"><form className="ei-form" data-testid="energy-appliance-form" onSubmit={event => { event.preventDefault(); save.mutate(); }}>
      <label>Appliance<select value={form.appliance_name} onChange={event => setForm({ ...form, appliance_name: event.target.value })}>{choices.map(name => <option key={name}>{name}</option>)}</select></label>
      {form.appliance_name === 'Other' && <label>Appliance name<input required maxLength={100} value={customName} onChange={event => setCustomName(event.target.value)}/></label>}
      <label>Power rating (watts)<input required type="number" min="1" max="100000" step="1" placeholder="e.g. 2000" value={form.power_watts} onChange={event => setForm({ ...form, power_watts: event.target.value })}/></label>
      <label>Operating hours per day<input required type="number" min="0" max="24" step="0.1" placeholder="e.g. 5" value={form.operating_hours} onChange={event => setForm({ ...form, operating_hours: event.target.value })}/></label>
      <label>Quantity<input required type="number" min="1" max="100" step="1" value={form.quantity} onChange={event => setForm({ ...form, quantity: event.target.value })}/></label>
      <label>Duty cycle (%)<input required type="number" min="0" max="100" step="1" value={form.duty_cycle} onChange={event => setForm({ ...form, duty_cycle: event.target.value })}/></label>
      <p className="ei-help-text ei-full">For appliances that cycle, enter the hours connected and the proportion of time drawing rated power. For example, 24 hours at 25% duty cycle means 6 effective hours. Estimates use watts × hours × quantity × duty cycle ÷ 1,000.</p>
      <div className="ei-form-actions"><button className="button-primary" disabled={pending}><Plus size={15}/>{save.isPending ? 'Saving…' : editing ? 'Update appliance' : 'Add appliance'}</button>{editing && <button type="button" className="button-outline" disabled={pending} onClick={() => { setEditing(null); setForm(blank); setCustomName(''); }}>Cancel edit</button>}</div>{save.isError && <p className="form-error ei-full" role="alert">{save.error.message}</p>}{save.isSuccess && <p className="form-success ei-full" role="status">Appliance estimates updated.</p>}
    </form><div className="ei-model-summary">{appliances.isPending ? <p className="ei-help-text" role="status">Loading appliance estimates…</p> : appliances.isError ? <p className="form-error" role="alert">{appliances.error.message}</p> : data && <><div className="ei-mini-stats"><div><span>Modelled per day</span><b>{energyNumber(data.total_daily_kwh)} kWh</b></div><div><span>Modelled per {data.days_in_month} days</span><b>{energyNumber(data.total_monthly_kwh)} kWh</b></div><div><span>Estimated monthly cost</span><b>{energyMoney(data.total_monthly_cost_rand)}</b></div></div>{data.appliances.length ? <div className="ei-appliance-chart" aria-label="Estimated daily electricity use by appliance"><ResponsiveContainer width="100%" height="100%"><BarChart layout="vertical" data={[...data.appliances].filter(item => item.daily_kwh != null).sort((a, b) => (b.daily_kwh ?? 0) - (a.daily_kwh ?? 0)).slice(0, 8)} margin={{ top: 10, right: 25, left: 0, bottom: 5 }}><CartesianGrid stroke="#edf1f5" horizontal={false}/><XAxis type="number" tick={{ fontSize: 10 }} unit=" kWh"/><YAxis type="category" dataKey="appliance_name" width={105} tick={{ fontSize: 10 }} axisLine={false} tickLine={false}/><Tooltip formatter={(value: number) => [`${energyNumber(value, 2)} kWh / day`, 'Estimated use']}/><Bar dataKey="daily_kwh" fill="#d4a33d" radius={[0, 4, 4, 0]} maxBarSize={22}/></BarChart></ResponsiveContainer></div> : <p className="ei-help-text">Add appliances to compare their estimated daily use.</p>}<p className="ei-help-text">{data.disclaimer}</p></>}</div></div>
    {data?.appliances.length ? <div className="ei-table-scroll"><table><thead><tr><th>Appliance</th><th>Power / effective hours</th><th>Daily estimate</th><th>Monthly estimate</th><th>Estimated monthly cost</th><th>Modelled share</th><th>Actions</th></tr></thead><tbody>{data.appliances.map(item => <tr key={item.id}><td>{item.appliance_name} × {item.quantity}</td><td>{energyNumber(item.power_watts, 0)} W / {energyNumber(item.effective_hours)} h</td><td>{energyNumber(item.daily_kwh, 2)} kWh</td><td>{energyNumber(item.monthly_kwh)} kWh</td><td>{energyMoney(item.monthly_cost_rand)}</td><td>{energyNumber(item.percentage_of_modelled_total)}%</td><td><div className="ei-table-actions"><button className="text-link" aria-label={`Edit ${item.appliance_name}`} disabled={pending} onClick={() => edit(item)}><Pencil size={14}/></button><button className="text-link ei-delete" aria-label={`Remove ${item.appliance_name}`} disabled={pending} onClick={() => remove.mutate(item.id)}><Trash2 size={14}/></button></div></td></tr>)}</tbody></table></div> : null}{remove.isError && <p className="form-error" role="alert">{remove.error.message}</p>}
  </section>;
}

export function EnergyAdvisor({ sessionId }: { sessionId?: string }) {
  const query = useQuery({ queryKey: ['energy', 'advisor', sessionId ?? 'actual'], queryFn: () => getJson<EnergyAdvisorResponse>(`/energy/advisor${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''}`), staleTime: 60_000 });
  const data = query.data;
  return <section className="panel ei-recommendations" id="energy-advisor"><div className="ei-section-heading"><div className="ei-panel-heading"><span className="panel-icon ei-saving-icon"><Lightbulb size={18}/></span><div><h3>EcoPulse AI Energy Saving Advisor</h3><p>Practical actions with savings based on your household estimates.</p></div></div><button className="button-outline" disabled={query.isFetching} onClick={() => query.refetch()}>Refresh advice</button></div>{sessionId && <EnergyDemoBadge/>}
    {query.isPending && <p className="ei-help-text" role="status">Loading saving opportunities…</p>}{query.isError && <p className="form-error" role="alert">{query.error.message}</p>}
    {data?.insight && <><span className={`ei-source ${data.insight.source}`}>{data.insight.source === 'gemini' ? 'Gemini analysis' : 'Rule-based guidance'}</span><p className="ei-advisor-summary">{data.insight.analysis.summary}</p>{data.insight.analysis.recommendations.length > 0 && <ul>{data.insight.analysis.recommendations.map((item, index) => <li key={index}>{item}</li>)}</ul>}</>}
    {data?.recommendations?.length ? <div className="ei-advice-grid">{data.recommendations.map((item, index) => <article key={item.appliance_id ?? index}><h4>{item.action}</h4><b>{energyNumber(item.energy_reduction_kwh_per_day, 2)} kWh / day · {energyMoney(item.cost_reduction_rand_per_day)} / day</b><p>{item.assumptions}</p><small>Uncertainty: {item.confidence}</small></article>)}</div> : data && <p className="ei-help-text">Add appliance estimates and analyse a recorded day for personalised opportunities. Numerical savings require a configured appliance and tariff.</p>}
    <p className="ei-help-text">{data?.disclaimer ?? 'Potential savings are estimates based on your entered appliance ratings, operating hours and duty cycle. Avoid electrical modifications; consult a qualified professional when needed.'}</p>
  </section>;
}

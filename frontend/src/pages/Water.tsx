import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Droplets, Plus, Waves } from 'lucide-react';
import { getAccessToken } from '../services/auth';
import { getJson, postJson } from '../services/api';
import './water.css';

type Readings = { items: { consumption: number; previous_date: string; current_date: string }[]; count: number };

export function Water() {
  const [form, setForm] = useState({ previous_reading: '', current_reading: '', previous_date: '', current_date: '', household_size: '1' });
  const qc = useQueryClient();
  const readings = useQuery({ queryKey: ['water', 'readings'], queryFn: () => getJson<Readings>('/water/readings') });
  const latest = readings.data?.items[0];
  const days = latest ? Math.round((Date.parse(latest.current_date) - Date.parse(latest.previous_date)) / 86400000) : 0;
  const dailyLitres = latest && days > 0 ? latest.consumption * 1000 / days : null;
  const projectedUsage = dailyLitres === null ? null : dailyLitres * 30 / 1000;
  const mutation = useMutation({
    mutationFn: () => postJson('/water/readings', form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['water'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
      setForm({ previous_reading: '', current_reading: '', previous_date: '', current_date: '', household_size: '1' });
    },
  });
  const usage = Number(form.current_reading) - Number(form.previous_reading);
  const hasBothReadings = form.previous_reading !== '' && form.current_reading !== '';
  const readingsOutOfOrder = hasBothReadings && Number(form.current_reading) < Number(form.previous_reading);
  const datesOutOfOrder = form.previous_date !== '' && form.current_date !== '' && form.current_date <= form.previous_date;

  return <div className="content household-water">
    <div className="page-heading"><div><p className="eyebrow">HOUSEHOLD SERVICES</p><h1>Water management</h1><p className="muted">Record your meter readings to understand your household usage.</p></div></div>
    <section className="water-intro"><span className="stat-icon blue"><Droplets size={24}/></span><div><h2>Your water, made clearer</h2><p>Add two meter readings. See what you used and an estimate for the next 30 days.</p></div></section>
    <div className="stats-grid water-summary">
      <article className="stat-card"><span className="stat-icon blue"><Droplets size={19}/></span><p className="stat-label">Latest consumption</p><div className="stat-value">{latest ? latest.consumption.toFixed(3) : '—'}<small> kL</small></div><p className="stat-caption">{latest ? `Reading recorded ${latest.current_date}` : readings.isError ? 'Readings unavailable' : 'Add readings to calculate usage'}</p></article>
      <article className="stat-card"><span className="stat-icon blue"><Waves size={19}/></span><p className="stat-label">Average daily use</p><div className="stat-value">{dailyLitres === null ? '-' : Math.round(dailyLitres).toLocaleString()}<small> litres</small></div><p className="stat-caption">{days > 0 ? `Based on your latest ${days}-day reading period` : 'Add readings on different dates to see your average'}</p></article>
      <article className="stat-card"><span className="stat-icon blue"><Droplets size={19}/></span><p className="stat-label">Next 30 days at this pace</p><div className="stat-value">{projectedUsage === null ? '-' : projectedUsage.toFixed(2)}<small> kL</small></div><p className="stat-caption">A usage estimate, assuming your daily use stays the same.</p></article>
    </div>
    <div className="water-workspace"><div className="form-panel panel">
      <div className="panel-header"><div><div className="panel-title"><span className="panel-icon water-icon"><Plus size={17}/></span><h3>Record meter reading</h3></div><p>Copy the total shown on your meter, not the amount used. 1 kL equals 1,000 litres.</p></div></div>
      <form className="reading-form" onSubmit={e => { e.preventDefault(); mutation.mutate(); }}>
        <label>Earlier meter reading (kL)<input required min="0" step="0.001" type="number" value={form.previous_reading} onChange={e => setForm({ ...form, previous_reading: e.target.value })} placeholder="e.g. 120.500"/></label>
        <label>Latest meter reading (kL)<input required min={form.previous_reading || '0'} step="0.001" type="number" value={form.current_reading} onChange={e => setForm({ ...form, current_reading: e.target.value })} placeholder="e.g. 128.250"/></label>
        {readingsOutOfOrder && <p className="form-error" role="alert">Current meter reading must be at least the previous reading. Check that the two values are in the correct date order.</p>}
        <label>Earlier reading date<input required type="date" value={form.previous_date} onChange={e => setForm({ ...form, previous_date: e.target.value })}/></label>
        <label>Latest reading date<input required type="date" value={form.current_date} onChange={e => setForm({ ...form, current_date: e.target.value })}/></label>
        {datesOutOfOrder && <p className="form-error" role="alert">Latest reading date must be after the previous reading date.</p>}
        <label>Household size<input required min="1" max="30" type="number" value={form.household_size} onChange={e => setForm({ ...form, household_size: e.target.value })}/></label>
        <div className="reading-preview"><small>CALCULATED CONSUMPTION</small><b>{hasBothReadings && Number.isFinite(usage) && usage >= 0 ? usage.toFixed(3) : '—'} kL</b><span>Latest reading minus earlier reading</span></div>
        <button className="button-primary" type="submit" disabled={mutation.isPending || readingsOutOfOrder || datesOutOfOrder}><Plus size={16}/>{mutation.isPending ? 'Saving…' : 'Save reading'}</button>
        {mutation.isError && <p className="form-error" role="alert">{(mutation.error as Error).message}</p>}
        {mutation.isSuccess && <p className="form-success">Reading saved successfully.</p>}
      </form>
    </div>
    <aside className="panel water-help"><h3>How to read your meter</h3><ol><li>Find the total measured in kilolitres (kL).</li><li>Enter the earlier and latest totals with the dates you recorded them.</li><li>Save to see your household usage and daily average.</li></ol><div className="water-help-note"><b>About your estimate</b><p>Your 30-day estimate uses your latest reading period. It is not a water bill or an AI prediction. Changes in household use will change the result.</p></div><p>Unexpectedly high use? Check for dripping taps or a running toilet.</p></aside></div>
    <section className="panel water-history"><h3>Recent readings</h3>{readings.isPending ? <p role="status">Loading readings...</p> : readings.isError ? <p role="alert">{readings.error.message}</p> : readings.data?.items.length ? <div className="workspace-table-wrap"><table className="workspace-table"><thead><tr><th>Reading period</th><th>Water used</th></tr></thead><tbody>{readings.data.items.slice(0,5).map((item,index)=><tr key={index}><td>{item.previous_date} to {item.current_date}</td><td>{item.consumption.toFixed(3)} kL</td></tr>)}</tbody></table></div> : <p>Your saved readings will appear here.</p>}</section>
    <div className="privacy-note">{getAccessToken() ? 'Readings are stored with your account and are visible only in your resident workspace.' : 'Local development mode stores readings in SQLite without account-level access controls. Do not enter real household data.'}</div>
  </div>;
}

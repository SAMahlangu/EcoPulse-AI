import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Gauge, Plus, Settings2 } from 'lucide-react';
import { getJson, postJson, putJson } from '../../services/api';
import type { WaterDay, WaterHousehold, WaterMeterReading } from '../../types/water';
import { waterDate, waterNumber } from '../../types/water';

export function WaterSettings({ household }: { household: WaterHousehold }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({ name: household.name, household_size: String(household.household_size), daily_target_litres: String(household.daily_target_litres), tariff_per_kl: household.tariff_per_kl == null ? '' : String(household.tariff_per_kl), timezone: household.timezone });
  const save = useMutation({
    mutationFn: () => putJson('/water/household', { ...form, household_size: Number(form.household_size), daily_target_litres: Number(form.daily_target_litres), tariff_per_kl: form.tariff_per_kl === '' ? 0 : Number(form.tariff_per_kl) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['water'] }),
  });
  return <details className="panel wi-settings"><summary><span><Settings2 size={17}/> Household target and tariff</span><span className="wi-help-text">{waterNumber(household.daily_target_litres)} L / day · {household.household_size} {household.household_size === 1 ? 'person' : 'people'}</span></summary>
    <form className="wi-form" data-testid="water-settings-form" onSubmit={event => { event.preventDefault(); save.mutate(); }}>
      <label>Household name<input required maxLength={80} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })}/></label>
      <label>Household size<input required type="number" min="1" max="30" step="1" value={form.household_size} onChange={e => setForm({ ...form, household_size: e.target.value })}/></label>
      <label>Daily water target (litres)<input required type="number" min="1" max="100000" step="1" value={form.daily_target_litres} onChange={e => setForm({ ...form, daily_target_litres: e.target.value })}/></label>
      <label>Tariff (R per kL)<input type="number" min="0" max="10000" step="0.01" placeholder="Optional, e.g. 25.50" value={form.tariff_per_kl} onChange={e => setForm({ ...form, tariff_per_kl: e.target.value })}/></label>
      <label>Household timezone<select value={form.timezone} onChange={e => setForm({ ...form, timezone: e.target.value })}><option value="Africa/Johannesburg">Africa/Johannesburg (SAST)</option>{form.timezone !== 'Africa/Johannesburg' && <option value={form.timezone}>{form.timezone}</option>}<option value="UTC">UTC</option></select></label>
      <p className="wi-help-text">Cost uses your entered flat tariff. It excludes fixed charges and municipal tariff tiers. Targets are saved with each day's record.</p>
      <div className="wi-form-actions"><button className="button-primary" disabled={save.isPending}><Check size={15}/>{save.isPending ? 'Saving…' : 'Save household settings'}</button>{save.isSuccess && <span className="form-success" role="status">Settings saved.</span>}</div>
      {save.isError && <p className="form-error" role="alert">{save.error.message}</p>}
    </form>
  </details>;
}

export function WaterDailyForm({ today, editDay, onSaved, onCancel }: { today: string; editDay: WaterDay | null; onSaved: () => void; onCancel: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({ date: editDay?.date ?? today, litres: editDay?.litres == null ? '' : String(editDay.litres) });
  const save = useMutation({
    mutationFn: () => editDay?.id && editDay.data_source === 'manual' ? putJson(`/water/consumption/${encodeURIComponent(editDay.id)}`, { date: form.date, litres: Number(form.litres) }) : postJson('/water/consumption', { date: form.date, litres: Number(form.litres) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['water'] }); qc.invalidateQueries({ queryKey: ['dashboard'] }); setForm({ date: today, litres: '' }); onSaved(); },
  });
  return <section className="panel wi-record-panel"><div className="wi-panel-heading"><span className="panel-icon water-icon"><Plus size={18}/></span><div><h3>{editDay ? 'Edit daily consumption' : 'Record daily consumption'}</h3><p>Enter the litres used on a particular day.</p></div></div>
    <form className="wi-form" data-testid="water-daily-form" onSubmit={event => { event.preventDefault(); save.mutate(); }}>
      <label>Consumption date<input required type="date" max={today} value={form.date} disabled={Boolean(editDay)} onChange={e => setForm({ ...form, date: e.target.value })}/></label>
      <label>Water consumed (litres)<input required type="number" min="0" max="1000000" step="0.1" placeholder="e.g. 590" value={form.litres} onChange={e => setForm({ ...form, litres: e.target.value })}/></label>
      <p className="wi-help-text wi-full">A saved daily value takes priority over a meter estimate for that date. Existing daily entries stay on their original date when edited.</p>
      <div className="wi-form-actions"><button className="button-primary" disabled={save.isPending}><Plus size={15}/>{save.isPending ? 'Saving…' : editDay ? 'Update consumption' : 'Save daily consumption'}</button>{editDay && <button type="button" className="button-outline" onClick={onCancel} disabled={save.isPending}>Cancel edit</button>}</div>
      {save.isError && <p className="form-error wi-full" role="alert">{save.error.message}</p>}{save.isSuccess && <p className="form-success wi-full" role="status">Daily consumption saved.</p>}
    </form>
  </section>;
}

export function WaterCumulativeForm() {
  const qc = useQueryClient();
  const [form, setForm] = useState({ reading_at: '', cumulative_kl: '', is_reset: false, notes: '' });
  const readings = useQuery({ queryKey: ['water', 'cumulative-meter-readings'], queryFn: () => getJson<{ items: WaterMeterReading[] }>('/water/meter-readings?limit=8') });
  const save = useMutation({
    mutationFn: () => postJson('/water/meter-readings', { ...form, reading_at: new Date(form.reading_at).toISOString(), cumulative_kl: Number(form.cumulative_kl) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['water'] }); setForm({ reading_at: '', cumulative_kl: '', is_reset: false, notes: '' }); },
  });
  return <section className="panel wi-record-panel"><div className="wi-panel-heading"><span className="panel-icon water-icon"><Gauge size={18}/></span><div><h3>Add a cumulative meter reading</h3><p>Copy the total displayed on the meter in kilolitres.</p></div></div>
    <form className="wi-form" data-testid="water-cumulative-form" onSubmit={event => { event.preventDefault(); save.mutate(); }}>
      <label>Reading date and time<input required type="datetime-local" value={form.reading_at} onChange={e => setForm({ ...form, reading_at: e.target.value })}/></label>
      <label>Cumulative meter total (kL)<input required type="number" min="0" max="1000000000" step="0.001" placeholder="e.g. 128.250" value={form.cumulative_kl} onChange={e => setForm({ ...form, cumulative_kl: e.target.value })}/></label>
      <label className="wi-full">Notes (optional)<input maxLength={1000} placeholder="e.g. New meter installed" value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })}/></label>
      <label className="wi-checkbox wi-full"><input type="checkbox" checked={form.is_reset} onChange={e => setForm({ ...form, is_reset: e.target.checked })}/>This is the first reading after a meter reset or replacement.</label>
      <p className="wi-help-text wi-full">The first reading establishes a baseline. Usage across multiple days is allocated as an estimate and marked clearly. Reset readings start a new baseline. Times use your device's local timezone.</p>
      <div className="wi-form-actions"><button className="button-primary" disabled={save.isPending}><Plus size={15}/>{save.isPending ? 'Saving…' : 'Save cumulative reading'}</button></div>
      {save.isError && <p className="form-error wi-full" role="alert">{save.error.message}</p>}{save.isSuccess && <p className="form-success wi-full" role="status">Meter reading saved.</p>}
    </form>
    <details className="wi-meter-history"><summary>Recent cumulative readings</summary>{readings.isPending ? <p role="status">Loading readings…</p> : readings.isError ? <p className="form-error" role="alert">{readings.error.message}</p> : readings.data?.items.length ? <div className="wi-table-scroll"><table><thead><tr><th>Recorded at</th><th>Meter total</th><th>Type</th></tr></thead><tbody>{readings.data.items.map(item => <tr key={item.id}><td>{new Date(item.reading_at).toLocaleString('en-ZA', { dateStyle: 'medium', timeStyle: 'short' })}</td><td>{waterNumber(item.cumulative_kl, 3)} kL</td><td>{item.is_reset ? 'Reset baseline' : 'Reading'}</td></tr>)}</tbody></table></div> : <p className="wi-help-text">No cumulative readings recorded yet.</p>}</details>
  </section>;
}

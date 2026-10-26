import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Gauge, Plus, Settings2 } from 'lucide-react';
import { getJson, postJson, putJson } from '../../services/api';
import type { EnergyDay, EnergyHousehold, EnergyMeterReading } from '../../types/energy';
import { energyDate, energyNumber } from '../../types/energy';

export function EnergySettings({ household }: { household: EnergyHousehold }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({ name: household.name, household_size: String(household.household_size), daily_target_kwh: String(household.daily_target_kwh), tariff_per_kwh: household.tariff_per_kwh == null ? '' : String(household.tariff_per_kwh), timezone: household.timezone });
  const save = useMutation({
    mutationFn: () => putJson('/energy/household', { ...form, household_size: Number(form.household_size), daily_target_kwh: Number(form.daily_target_kwh), tariff_per_kwh: form.tariff_per_kwh === '' ? 0 : Number(form.tariff_per_kwh) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['energy'] }),
  });
  return <details className="panel ei-settings" id="energy-settings"><summary><span><Settings2 size={17}/> Household target and tariff</span><span className="ei-help-text">{energyNumber(household.daily_target_kwh)} kWh / day · {household.household_size} {household.household_size === 1 ? 'person' : 'people'}</span></summary>
    <form className="ei-form" data-testid="energy-settings-form" onSubmit={event => { event.preventDefault(); save.mutate(); }}>
      <label>Household name<input required maxLength={80} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })}/></label>
      <label>Household size<input required type="number" min="1" max="30" step="1" value={form.household_size} onChange={e => setForm({ ...form, household_size: e.target.value })}/></label>
      <label>Daily energy target (kWh)<input required type="number" min="0.1" max="100000" step="0.1" value={form.daily_target_kwh} onChange={e => setForm({ ...form, daily_target_kwh: e.target.value })}/></label>
      <label>Tariff (R per kWh)<input type="number" min="0" max="10000" step="0.01" placeholder="Optional, e.g. 2.75" value={form.tariff_per_kwh} onChange={e => setForm({ ...form, tariff_per_kwh: e.target.value })}/></label>
      <label>Household timezone<select value={form.timezone} onChange={e => setForm({ ...form, timezone: e.target.value })}><option value="Africa/Johannesburg">Africa/Johannesburg (SAST)</option>{form.timezone !== 'Africa/Johannesburg' && <option value={form.timezone}>{form.timezone}</option>}<option value="UTC">UTC</option></select></label>
      <p className="ei-help-text">Cost uses your entered flat tariff. It excludes fixed charges and municipal tariff tiers. Targets are saved with each day's record.</p>
      <div className="ei-form-actions"><button className="button-primary" disabled={save.isPending}><Check size={15}/>{save.isPending ? 'Saving…' : 'Save household settings'}</button>{save.isSuccess && <span className="form-success" role="status">Settings saved.</span>}</div>
      {save.isError && <p className="form-error" role="alert">{save.error.message}</p>}
    </form>
  </details>;
}

export function EnergyDailyForm({ today, editDay, onSaved, onCancel }: { today: string; editDay: EnergyDay | null; onSaved: () => void; onCancel: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({ date: editDay?.date ?? today, kwh: editDay?.kwh == null ? '' : String(editDay.kwh) });
  const save = useMutation({
    mutationFn: () => editDay?.id && editDay.data_source === 'manual' ? putJson(`/energy/consumption/${encodeURIComponent(editDay.id)}`, { date: form.date, kwh: Number(form.kwh) }) : postJson('/energy/consumption', { date: form.date, kwh: Number(form.kwh) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['energy'] }); qc.invalidateQueries({ queryKey: ['dashboard'] }); setForm({ date: today, kwh: '' }); onSaved(); },
  });
  return <section className="panel ei-record-panel" id="energy-daily-form"><div className="ei-panel-heading"><span className="panel-icon energy-icon"><Plus size={18}/></span><div><h3>{editDay ? 'Edit daily consumption' : 'Record daily consumption'}</h3><p>Enter the kilowatt-hours used on a particular day.</p></div></div>
    <form className="ei-form" data-testid="energy-daily-form" onSubmit={event => { event.preventDefault(); save.mutate(); }}>
      <label>Consumption date<input required type="date" max={today} value={form.date} disabled={Boolean(editDay)} onChange={e => setForm({ ...form, date: e.target.value })}/></label>
      <label>Energy consumed (kWh)<input required type="number" min="0" max="1000000" step="0.1" placeholder="e.g. 18.5" value={form.kwh} onChange={e => setForm({ ...form, kwh: e.target.value })}/></label>
      <p className="ei-help-text ei-full">A saved daily value takes priority over a meter estimate for that date. Existing daily entries stay on their original date when edited.</p>
      <div className="ei-form-actions"><button className="button-primary" disabled={save.isPending}><Plus size={15}/>{save.isPending ? 'Saving…' : editDay ? 'Update consumption' : 'Save daily consumption'}</button>{editDay && <button type="button" className="button-outline" onClick={onCancel} disabled={save.isPending}>Cancel edit</button>}</div>
      {save.isError && <p className="form-error ei-full" role="alert">{save.error.message}</p>}{save.isSuccess && <p className="form-success ei-full" role="status">Daily consumption saved.</p>}
    </form>
  </section>;
}

export function EnergyCumulativeForm() {
  const qc = useQueryClient();
  const [form, setForm] = useState({ reading_at: '', cumulative_kwh: '', is_reset: false, notes: '' });
  const readings = useQuery({ queryKey: ['energy', 'cumulative-meter-readings'], queryFn: () => getJson<{ items: EnergyMeterReading[] }>('/energy/meter-readings?limit=8') });
  const save = useMutation({
    mutationFn: () => postJson('/energy/meter-readings', { ...form, reading_at: new Date(form.reading_at).toISOString(), cumulative_kwh: Number(form.cumulative_kwh) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['energy'] }); setForm({ reading_at: '', cumulative_kwh: '', is_reset: false, notes: '' }); },
  });
  return <section className="panel ei-record-panel"><div className="ei-panel-heading"><span className="panel-icon energy-icon"><Gauge size={18}/></span><div><h3>Add a cumulative meter reading</h3><p>Copy the total displayed on the meter in kilowatt-hours (kWh).</p></div></div>
    <form className="ei-form" data-testid="energy-cumulative-form" onSubmit={event => { event.preventDefault(); save.mutate(); }}>
      <label>Reading date and time<input required type="datetime-local" value={form.reading_at} onChange={e => setForm({ ...form, reading_at: e.target.value })}/></label>
      <label>Cumulative meter total (kWh)<input required type="number" min="0" max="1000000000" step="0.001" placeholder="e.g. 1268.0" value={form.cumulative_kwh} onChange={e => setForm({ ...form, cumulative_kwh: e.target.value })}/></label>
      <label className="ei-full">Notes (optional)<input maxLength={1000} placeholder="e.g. New meter installed" value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })}/></label>
      <label className="ei-checkbox ei-full"><input type="checkbox" checked={form.is_reset} onChange={e => setForm({ ...form, is_reset: e.target.checked })}/>This is the first reading after a meter reset or replacement.</label>
      <p className="ei-help-text ei-full">The first reading establishes a baseline. Usage across multiple days is allocated as an estimate and marked clearly. Reset readings start a new baseline. Times use your device's local timezone.</p>
      <div className="ei-form-actions"><button className="button-primary" disabled={save.isPending}><Plus size={15}/>{save.isPending ? 'Saving…' : 'Save cumulative reading'}</button></div>
      {save.isError && <p className="form-error ei-full" role="alert">{save.error.message}</p>}{save.isSuccess && <p className="form-success ei-full" role="status">Meter reading saved.</p>}
    </form>
    <details className="ei-meter-history"><summary>Recent cumulative readings</summary>{readings.isPending ? <p role="status">Loading readings…</p> : readings.isError ? <p className="form-error" role="alert">{readings.error.message}</p> : readings.data?.items.length ? <div className="ei-table-scroll"><table><thead><tr><th>Recorded at</th><th>Meter total</th><th>Type</th></tr></thead><tbody>{readings.data.items.map(item => <tr key={item.id}><td>{new Date(item.reading_at).toLocaleString('en-ZA', { dateStyle: 'medium', timeStyle: 'short' })}</td><td>{energyNumber(item.cumulative_kwh, 3)} kWh</td><td>{item.is_reset ? 'Reset baseline' : 'Reading'}</td></tr>)}</tbody></table></div> : <p className="ei-help-text">No cumulative readings recorded yet.</p>}</details>
  </section>;
}

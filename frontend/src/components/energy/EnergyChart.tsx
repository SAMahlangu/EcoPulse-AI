import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { EnergyDay } from '../../types/energy';
import { energyDate, energyMoney, energyNumber } from '../../types/energy';

export function EnergyDemoBadge() { return <span className="ei-demo-badge">DEMO MODE — SIMULATED DATA</span>; }

function EnergyTooltip({ active, payload }: { active?: boolean; payload?: { payload: EnergyDay }[] }) {
  const day = payload?.[0]?.payload;
  if (!active || !day) return null;
  return <div className="ei-tooltip"><b>{energyDate(day.date)}</b>{day.is_simulation && <EnergyDemoBadge/>}<dl>
    <div><dt>{day.is_estimated ? 'Estimated consumption' : 'Consumption'}</dt><dd>{day.kwh == null ? 'Missing reading' : `${energyNumber(day.kwh, 2)} kWh`}</dd></div>
    <div><dt>Daily target</dt><dd>{energyNumber(day.target_kwh)} kWh</dd></div>
    <div><dt>Difference</dt><dd>{day.difference_kwh != null && day.difference_kwh > 0 ? '+' : ''}{energyNumber(day.difference_kwh)} kWh</dd></div>
    <div><dt>Estimated daily cost</dt><dd>{energyMoney(day.estimated_cost_rand)}</dd></div>
    {day.difference_kwh != null && day.difference_kwh > 0 && <div><dt>Additional cost above target</dt><dd>{energyMoney(day.additional_cost_rand)}</dd></div>}
    <div><dt>Status</dt><dd>{day.kwh == null ? 'No data' : day.status}</dd></div>
  </dl></div>;
}

export function EnergyChart({ series, simulation, selectedDate, onSelect }: { series: EnergyDay[]; simulation: boolean; selectedDate: string; onSelect: (date: string) => void }) {
  const values = series.map(day => ({ ...day, measured_kwh: day.is_estimated ? null : day.kwh, estimated_kwh: day.is_estimated ? day.kwh : null }));
  const dot = ({ cx, cy, payload }: { cx?: number; cy?: number; payload?: EnergyDay }) => {
    if (cx == null || cy == null || payload?.kwh == null) return <g/>;
    return <circle key={payload.date} cx={cx} cy={cy} r={payload.anomalous || selectedDate === payload.date ? 5 : 3} fill={payload.anomalous ? '#d64949' : '#1687bc'} stroke="#fff" strokeWidth={2}/>;
  };
  return <div className="ei-chart-wrapper">
    {simulation && <EnergyDemoBadge/>}
    <div className="ei-chart" data-testid="energy-chart" data-chart-start={series[0]?.date} data-chart-end={series[series.length - 1]?.date} aria-label={`${simulation ? 'Simulated' : 'Household'} daily electricity consumption in kilowatt-hours. Select a recorded date below for analysis.`}>
      <ResponsiveContainer width="100%" height="100%"><LineChart data={values} margin={{ top: 16, right: 18, left: 0, bottom: 8 }} onClick={state => { const day = state?.activePayload?.[0]?.payload as EnergyDay | undefined; if (day?.kwh != null) onSelect(day.date); }}>
        <CartesianGrid stroke="#e8eff3" vertical={false}/>
        <XAxis dataKey="date" tickFormatter={date => new Date(`${date}T12:00:00`).toLocaleDateString('en-ZA', { day: 'numeric', month: 'short' })} tick={{ fontSize: 10, fill: '#7c8e9b' }} axisLine={false} tickLine={false} minTickGap={24}/>
        <YAxis tick={{ fontSize: 10, fill: '#7c8e9b' }} axisLine={false} tickLine={false} width={52} label={{ value: 'kWh', angle: -90, position: 'insideLeft', style: { fill: '#7c8e9b', fontSize: 10 } }}/>
        <Tooltip content={<EnergyTooltip/>}/>
        <Line name={simulation ? 'Simulated consumption' : 'Recorded consumption'} dataKey="measured_kwh" stroke="#1687bc" strokeWidth={2.5} type="linear" dot={dot} activeDot={{ r: 6 }} connectNulls={false} isAnimationActive={false}/>
        <Line name="Estimated consumption" dataKey="estimated_kwh" stroke="#1687bc" strokeWidth={2.5} strokeDasharray="5 4" type="linear" dot={dot} connectNulls={false} isAnimationActive={false}/>
        <Line name="Daily target" dataKey="target_kwh" stroke="#3d9568" strokeWidth={1.8} strokeDasharray="6 5" dot={false} type="linear" isAnimationActive={false}/>
        <Line name="7-day moving average" dataKey="moving_average_kwh" stroke="#9271c6" strokeWidth={2} dot={false} type="linear" connectNulls={false} isAnimationActive={false}/>
      </LineChart></ResponsiveContainer>
    </div>
    <div className="ei-chart-legend"><span><i className="ei-key-actual"/>{simulation ? 'Simulated consumption' : 'Recorded consumption'}</span><span><i className="ei-key-estimated"/>Interval estimate</span><span><i className="ei-key-target"/>Daily target</span><span><i className="ei-key-average"/>7-day average</span><span><i className="ei-key-anomaly"/>Unusual consumption</span></div>
    <p className="ei-help-text">Gaps mean missing readings; zero means recorded zero use. Dashed blue values are daily allocations from meter intervals, rather than directly measured daily consumption.</p>
    <label className="ei-day-select">Select a day for analysis<select data-testid="energy-day-select" value={selectedDate} onChange={event => onSelect(event.target.value)}><option value="" disabled>Select a recorded date</option>{series.filter(day => day.kwh != null).map(day => <option value={day.date} key={day.date}>{energyDate(day.date)} — {energyNumber(day.kwh)} kWh{day.is_estimated ? ' (estimated)' : ''}{simulation ? ' (simulated)' : ''}</option>)}</select></label>
  </div>;
}

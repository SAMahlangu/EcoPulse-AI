import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { WaterDay } from '../../types/water';
import { waterDate, waterNumber } from '../../types/water';

export function DemoBadge() {
  return <span className="wi-demo-badge">DEMO MODE — SIMULATED DATA</span>;
}

function illustrativeLitres(day: WaterDay) {
  if (day.litres != null || day.target_litres <= 0) return null;
  // Stable, visual-only sample values. They never enter API payloads, summaries or analysis.
  let seed = 0;
  for (const character of day.date) seed = (seed * 31 + character.charCodeAt(0)) >>> 0;
  const fraction = (seed % 1000) / 1000;
  return Math.round(day.target_litres * (0.72 + fraction * 0.48) * 10) / 10;
}

export function WaterChart({ series, simulation, selectedDate, onSelect }: { series: WaterDay[]; simulation: boolean; selectedDate: string; onSelect: (date: string) => void }) {
  const chartData = series.map(day => ({ ...day,
    actual_litres: day.is_estimated ? null : day.litres,
    estimated_litres: day.is_estimated ? day.litres : null,
    illustrative_litres: simulation ? null : illustrativeLitres(day),
  }));
  const point = ({ cx, cy, payload }: { cx?: number; cy?: number; payload?: WaterDay }) => {
    if (cx == null || cy == null || payload?.litres == null) return <g/>;
    return <circle key={payload.date} cx={cx} cy={cy} r={payload.anomalous || selectedDate === payload.date ? 5 : 3} fill={payload.anomalous ? '#d64949' : '#1687bc'} stroke="#fff" strokeWidth={2}/>;
  };
  return <div className="wi-chart-wrapper">
    {simulation && <DemoBadge/>}
    <div className="wi-chart" data-testid="water-chart" data-chart-start={series[0]?.date} data-chart-end={series[series.length - 1]?.date} aria-label={`${simulation ? 'Simulated' : 'Household'} daily water consumption in litres. Select a recorded date below for analysis.`}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData} margin={{ top: 16, right: 18, left: 0, bottom: 8 }} onClick={state => { const day = state?.activePayload?.[0]?.payload as WaterDay | undefined; if (day?.litres != null) onSelect(day.date); }}>
          <CartesianGrid stroke="#e8eff3" vertical={false}/>
          <XAxis dataKey="date" tickFormatter={date => new Date(`${date}T12:00:00`).toLocaleDateString('en-ZA', { day: 'numeric', month: 'short' })} tick={{ fontSize: 10, fill: '#7c8e9b' }} axisLine={false} tickLine={false} minTickGap={24}/>
          <YAxis tick={{ fontSize: 10, fill: '#7c8e9b' }} axisLine={false} tickLine={false} width={55} label={{ value: 'Litres', angle: -90, position: 'insideLeft', style: { fill: '#7c8e9b', fontSize: 10 } }}/>
          <Tooltip labelFormatter={date => `${waterDate(String(date))}${simulation ? ' · SIMULATED DATA' : ''}`} formatter={(value: number, name: string) => [`${waterNumber(value, 1)} L`, name]} contentStyle={{ borderRadius: 10, border: '1px solid #dce8ef', fontSize: 11 }} itemStyle={{ padding: '3px 0' }}/>
          <Line name={simulation ? 'Simulated daily consumption' : 'Recorded daily consumption'} dataKey="actual_litres" stroke="#1687bc" strokeWidth={2.5} type="linear" dot={point} activeDot={{ r: 6 }} connectNulls={false}/>
          <Line name="Estimated daily consumption" dataKey="estimated_litres" stroke="#1687bc" strokeWidth={2.5} strokeDasharray="5 4" type="linear" dot={point} connectNulls={false}/>
          <Line name="Daily target" dataKey="target_litres" stroke="#3d9568" strokeWidth={1.8} strokeDasharray="6 5" dot={false} type="linear"/>
          {!simulation && <Line name="Illustrative sample (not recorded)" dataKey="illustrative_litres" stroke="#d99a32" strokeWidth={2} strokeDasharray="3 5" dot={false} type="linear" connectNulls={false}/>}
          <Line name="7-day moving average" dataKey="moving_average_litres" stroke="#9271c6" strokeWidth={2} dot={false} type="linear" connectNulls={false}/>
        </LineChart>
      </ResponsiveContainer>
    </div>
    <div className="wi-chart-legend" aria-hidden="true"><span><i className="wi-key-actual"/>{simulation ? 'Simulated consumption' : 'Recorded consumption'}</span>{!simulation && <span><i className="wi-key-sample"/>Illustrative sample (not recorded)</span>}<span><i className="wi-key-estimated"/>Estimated</span><span><i className="wi-key-target"/>Daily target</span><span><i className="wi-key-average"/>7-day average</span><span><i className="wi-key-anomaly"/>Unusual consumption</span></div>
    <p className="wi-help-text">{simulation ? 'Select a simulated point below for analysis.' : 'The orange dashed line fills days without readings with illustrative sample values. They are visual-only and are not saved or used for totals, alerts or AI analysis. Blue dashed values are estimates from meter intervals.'}</p>
    <label className="wi-day-select">Selected day<select data-testid="water-day-select" value={selectedDate} onChange={event => onSelect(event.target.value)}><option value="" disabled>Select a recorded date</option>{series.filter(day => day.litres != null).map(day => <option value={day.date} key={day.date}>{waterDate(day.date)} — {waterNumber(day.litres, 1)} L{day.is_estimated ? ' (estimated)' : ''}{simulation ? ' (simulated)' : ''}</option>)}</select></label>
  </div>;
}

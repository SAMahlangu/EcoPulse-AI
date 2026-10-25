import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, MessageSquareText, Sparkles } from 'lucide-react';
import { getJson, postJson } from '../../services/api';
import type { EnergyDay, EnergyExplanation, EnergyInsight, EnergyInsightsResponse } from '../../types/energy';
import { energyDate, energyMoney, energyNumber, energyReasons } from '../../types/energy';
import { EnergyDemoBadge } from './EnergyChart';

export function EnergyDayDetails({ day, sessionId, focusExplanation = 0 }: { day: EnergyDay; sessionId?: string; focusExplanation?: number }) {
  const qc = useQueryClient();
  const textarea = useRef<HTMLTextAreaElement>(null);
  const [reasons, setReasons] = useState<string[]>([]);
  const [text, setText] = useState('');
  const context = { date: day.date, ...(sessionId ? { session_id: sessionId } : {}) };
  const queryKey = ['energy', 'insights', sessionId ?? 'actual', day.date];
  const query = useQuery({ queryKey, queryFn: () => getJson<EnergyInsightsResponse>(`/energy/insights?date=${encodeURIComponent(day.date)}${sessionId ? `&session_id=${encodeURIComponent(sessionId)}` : ''}`), staleTime: 60_000 });
  useEffect(() => { if (query.data) { setReasons(query.data.explanation?.reasons ?? []); setText(query.data.explanation?.text ?? ''); } }, [query.data?.explanation]);
  useEffect(() => { if (focusExplanation) textarea.current?.focus(); }, [focusExplanation]);
  const analyse = useMutation({ mutationFn: () => postJson<EnergyInsight>('/energy/ai/analyse', context), onSuccess: insight => { qc.setQueryData<EnergyInsightsResponse>(queryKey, previous => ({ items: [insight], explanation: previous?.explanation ?? null })); qc.invalidateQueries({ queryKey: ['energy', 'advisor'] }); } });
  const explanation = useMutation({ mutationFn: () => postJson<{ explanation: EnergyExplanation; insight: EnergyInsight }>('/energy/explanations', { ...context, reasons, text }), onSuccess: result => { qc.setQueryData(queryKey, { items: [result.insight], explanation: result.explanation }); qc.invalidateQueries({ queryKey: ['energy', 'advisor'] }); } });
  const insight = query.data?.items[0];
  const pending = analyse.isPending || explanation.isPending;
  return <div className="ei-detail-grid" id="energy-day-details">
    <section className="panel ei-analysis" data-testid="energy-analysis"><div className="ei-panel-heading"><span className="panel-icon ei-ai-icon"><Sparkles size={18}/></span><div><h3>EcoPulse AI Energy Analysis</h3><p>{energyDate(day.date)}</p></div></div>{day.is_simulation && <EnergyDemoBadge/>}
      <div className="ei-confirmed-facts"><h4><CheckCircle2 size={15}/>Calculated consumption facts</h4><dl>
        <div><dt>{day.is_estimated ? 'Estimated daily allocation' : day.is_simulation ? 'Simulated consumption' : 'Recorded consumption'}</dt><dd>{energyNumber(day.kwh)} kWh</dd></div>
        <div><dt>Daily target</dt><dd>{energyNumber(day.target_kwh)} kWh</dd></div>
        <div><dt>Difference from target</dt><dd>{day.difference_kwh != null && day.difference_kwh > 0 ? '+' : ''}{energyNumber(day.difference_kwh)} kWh ({energyNumber(day.percentage_vs_target)}%)</dd></div>
        <div><dt>Estimated daily cost</dt><dd>{energyMoney(day.estimated_cost_rand)}</dd></div>
        {day.difference_kwh != null && day.difference_kwh > 0 && <div><dt>Additional estimated cost above target</dt><dd>{energyMoney(day.additional_cost_rand)}</dd></div>}
        <div><dt>Status</dt><dd>{day.status}</dd></div>
      </dl>{day.is_estimated && <p className="ei-help-text">This is allocated from a meter interval. It is not a directly measured daily value.</p>}</div>
      {query.isPending && <p className="ei-help-text" role="status">Looking for saved analysis…</p>}{query.isError && <p className="form-error" role="alert">Saved analysis unavailable: {query.error.message}</p>}
      {insight ? <div className="ei-insight-copy"><span className={`ei-source ${insight.source}`}>{insight.source === 'gemini' ? 'Gemini analysis' : 'Rule-based fallback · Gemini unavailable or disabled'}</span><p>{insight.analysis.summary}</p><h4>Possible explanations</h4>{insight.analysis.possible_causes.length ? <ul>{insight.analysis.possible_causes.map((cause, index) => <li key={index}>{cause}</li>)}</ul> : <p>Available data cannot identify a particular appliance or cause.</p>}<p className="ei-caveat"><AlertTriangle size={15}/>{insight.analysis.caveat}</p><small>Saved {new Date(insight.generated_at).toLocaleString('en-ZA')}</small>{insight.analysis.recommendations.length > 0 && <><h4>Recommended next steps</h4><ul>{insight.analysis.recommendations.map((item, index) => <li key={index}>{item}</li>)}</ul></>}</div> : <p className="ei-help-text">Analyse this day to understand patterns and practical next steps. Figures come from your saved readings and configured tariff.</p>}
      <div className="ei-actions"><button className="button-primary" disabled={pending} onClick={() => analyse.mutate()}><Sparkles size={15}/>{analyse.isPending ? 'Analysing…' : 'Analyse with Gemini'}</button><button className="button-outline" onClick={() => document.getElementById('energy-appliances')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>Analyse Appliances</button></div>{analyse.isError && <p className="form-error" role="alert">{analyse.error.message}</p>}
      <p className="ei-help-text">Consumption patterns and household explanations suggest possibilities. They cannot verify which appliance caused an increase.</p>
    </section>
    <section className="panel ei-explanation"><div className="ei-panel-heading"><span className="panel-icon energy-icon"><MessageSquareText size={18}/></span><div><h3>Why was your electricity consumption higher today?</h3><p>Add context for {energyDate(day.date)}. You can update it later.</p></div></div>{day.is_simulation && <EnergyDemoBadge/>}
      <form data-testid="energy-explanation-form" onSubmit={event => { event.preventDefault(); explanation.mutate(); }}><fieldset disabled={pending || query.isPending}><legend>Select all activities that apply</legend><div className="ei-reason-grid">{energyReasons.map(([value, label]) => <label key={value} className={`ei-reason ${reasons.includes(value) ? 'selected' : ''}`}><input type="checkbox" checked={reasons.includes(value)} onChange={event => setReasons(previous => event.target.checked ? [...previous, value] : previous.filter(reason => reason !== value))}/>{label}</label>)}</div><label className="ei-text-label">Tell EcoPulse AI what happened today<textarea ref={textarea} rows={4} maxLength={2000} value={text} onChange={event => setText(event.target.value)} placeholder="We used the geyser for longer than usual because we had visitors."/></label></fieldset><p className="ei-help-text">Your explanation is household context, rather than a verified cause.</p><button className="button-primary" disabled={pending || query.isPending || (!reasons.length && !text.trim())}><Sparkles size={15}/>{explanation.isPending ? 'Saving and analysing…' : 'Analyse My Explanation'}</button>{explanation.isError && <p className="form-error" role="alert">{explanation.error.message}</p>}{explanation.isSuccess && <p className="form-success" role="status">Explanation saved and analysis updated.</p>}</form>
    </section>
  </div>;
}

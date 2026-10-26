import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, MessageSquareText, Sparkles } from 'lucide-react';
import { getJson, postJson } from '../../services/api';
import type { EnergyDay, EnergyExplanation, EnergyInsight, EnergyInsightsResponse } from '../../types/energy';
import { energyDate, energyMoney, energyNumber, energyReasons } from '../../types/energy';
import { EnergyDemoBadge } from './EnergyChart';
import './EnergyExplanation.css';

export function EnergyDayDetails({ day, sessionId, focusExplanation = 0 }: { day: EnergyDay; sessionId?: string; focusExplanation?: number }) {
  const qc = useQueryClient();
  const textarea = useRef<HTMLTextAreaElement>(null);
  const explanationResult = useRef<HTMLElement>(null);
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
  const savedExplanation = query.data?.explanation;
  const sortedReasons = [...reasons].sort();
  const savedReasons = [...(savedExplanation?.reasons ?? [])].sort();
  const explanationMatchesSaved = Boolean(savedExplanation)
    && text === savedExplanation?.text
    && sortedReasons.length === savedReasons.length
    && sortedReasons.every((reason, index) => reason === savedReasons[index]);
  const explanationInsight = explanation.data?.insight
    ?? (explanationMatchesSaved ? insight : undefined);
  const pending = analyse.isPending || explanation.isPending;
  useEffect(() => {
    if (!explanation.data || !explanationResult.current) return;
    const frame = requestAnimationFrame(() => {
      const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      explanationResult.current?.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'nearest' });
      explanationResult.current?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [explanation.data]);
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
    <section className="panel ei-explanation"><div className="ei-panel-heading"><span className="panel-icon energy-icon"><MessageSquareText size={18}/></span><div><h3>Why was your electricity use higher?</h3><p>Add context for {energyDate(day.date)}. You can update it later.</p></div></div>{day.is_simulation && <EnergyDemoBadge/>}
      <form data-testid="energy-explanation-form" aria-busy={explanation.isPending} onSubmit={event => { event.preventDefault(); explanation.mutate(); }}>
        {query.isPending && <p className="ei-analysis-loading" role="status">Loading your saved note…</p>}
        <fieldset disabled={pending || query.isPending}>
          <legend>What may have increased your use?</legend>
          <p className="ei-form-tip">Choose any activities that apply, or just write a note below.</p>
          <div className="ei-reason-grid">{energyReasons.map(([value, label]) => <label key={value} className={`ei-reason ${reasons.includes(value) ? 'selected' : ''}`}><input type="checkbox" checked={reasons.includes(value)} onChange={event => { explanation.reset(); setReasons(previous => event.target.checked ? [...previous, value] : previous.filter(reason => reason !== value)); }}/>{label}</label>)}</div>
          <label className="ei-text-label">What happened at home? <span>Optional if you selected an activity above.</span><textarea ref={textarea} rows={3} maxLength={2000} value={text} onChange={event => { explanation.reset(); setText(event.target.value); }} placeholder="For example: We had visitors, so the geyser stayed on longer."/></label>
        </fieldset>
        <p className="ei-help-text">Your note gives helpful context. It does not prove which appliance caused the increase.</p>
        <button className="button-primary" type="submit" disabled={pending || query.isPending || (!reasons.length && !text.trim())}><Sparkles size={15}/>{explanation.isPending ? 'Saving and preparing your analysis…' : 'Analyse My Explanation'}</button>
        {!reasons.length && !text.trim() && <p className="ei-form-tip ei-required-tip">Select an activity or enter a short note to continue.</p>}
        {explanation.isPending && <p className="ei-analysis-loading" role="status">Checking the day’s recorded use and your household context…</p>}
        {explanation.isError && <div className="ei-explanation-error"><p className="form-error" role="alert">We couldn’t prepare your analysis. Your note is still here. Check your connection and try again.</p><details><summary>More details</summary><p>{explanation.error.message}</p></details></div>}
        {explanationInsight && <>
          <p className="ei-result-status" role="status">Your note is saved. Here’s what the readings and your explanation suggest.</p>
          <section ref={explanationResult} className="ei-explanation-result" data-testid="energy-explanation-analysis" aria-live="polite" aria-labelledby="ei-explanation-result-title" tabIndex={-1}>
          <div className="ei-result-heading"><h4 id="ei-explanation-result-title"><CheckCircle2 size={17}/> Your analysis</h4><span className={`ei-source ${explanationInsight.source}`}>{explanationInsight.source === 'gemini' ? 'AI analysis' : 'Guidance from saved data'}</span></div>
          <p className="ei-result-summary">{explanationInsight.analysis.summary}</p>
          <h5>What may explain the increase?</h5>
          {explanationInsight.analysis.possible_causes.length
            ? <ul>{explanationInsight.analysis.possible_causes.map((cause, index) => <li key={index}>{cause}</li>)}</ul>
            : <p className="ei-result-empty">There is not enough information to suggest a specific cause yet.</p>}
          {explanationInsight.analysis.recommendations.length > 0 && <><h5>What you can do next</h5><ul>{explanationInsight.analysis.recommendations.map((item, index) => <li key={index}>{item}</li>)}</ul></>}
          <p className="ei-caveat"><AlertTriangle size={15}/>{explanationInsight.analysis.caveat}</p>
          <small>Saved {new Date(explanationInsight.generated_at).toLocaleString('en-ZA')}</small>
          </section>
        </>}
      </form>
    </section>
  </div>;
}

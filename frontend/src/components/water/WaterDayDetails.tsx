import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, ArrowRight, CheckCircle2, Lightbulb, MessageSquareText, Sparkles, X } from 'lucide-react';
import { getJson, postJson } from '../../services/api';
import type { WaterDay, WaterExplanation, WaterInsight, WaterInsightsResponse } from '../../types/water';
import { waterDate, waterNumber, waterReasons } from '../../types/water';
import { DemoBadge } from './WaterChart';

export function WaterDayDetails({ day, sessionId, focusExplanation = 0, investigateRequest = 0, onInvestigationHandled }: { day: WaterDay; sessionId?: string; focusExplanation?: number; investigateRequest?: number; onInvestigationHandled?: () => void }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const textRef = useRef<HTMLTextAreaElement>(null);
  const [reasons, setReasons] = useState<string[]>([]);
  const [text, setText] = useState('');
  const [preview, setPreview] = useState(false);
  const context = sessionId ? { date: day.date, session_id: sessionId } : { date: day.date };
  const queryKey = ['water', 'insights', sessionId ?? 'actual', day.date];
  const insightQuery = useQuery({ queryKey, queryFn: () => getJson<WaterInsightsResponse>(`/water/insights?date=${encodeURIComponent(day.date)}${sessionId ? `&session_id=${encodeURIComponent(sessionId)}` : ''}`), staleTime: 60_000 });
  useEffect(() => {
    if (insightQuery.data) { setReasons(insightQuery.data.explanation?.reasons ?? []); setText(insightQuery.data.explanation?.text ?? ''); }
  }, [insightQuery.data?.explanation]);
  useEffect(() => { if (focusExplanation) textRef.current?.focus(); }, [focusExplanation]);
  const analyse = useMutation({
    mutationFn: () => postJson<WaterInsight>('/water/ai/analyse', context),
    onSuccess: insight => qc.setQueryData<WaterInsightsResponse>(queryKey, previous => ({ items: [insight], explanation: previous?.explanation ?? null })),
  });
  const explanation = useMutation({
    mutationFn: () => postJson<{ explanation: WaterExplanation; insight: WaterInsight }>('/water/explanations', { ...context, reasons, text }),
    onSuccess: result => { qc.setQueryData(queryKey, { items: [result.insight], explanation: result.explanation }); qc.invalidateQueries({ queryKey: ['water', 'dashboard'] }); },
  });
  const insight = insightQuery.data?.items[0];
  const pending = analyse.isPending || explanation.isPending;
  const investigate = () => {
    if (day.is_simulation || sessionId) { setPreview(true); return; }
    navigate('/resident/report-incident', { state: { waterInvestigation: { date: day.date, litres: day.litres, target_litres: day.target_litres, is_simulation: false } } });
  };
  useEffect(() => { if (investigateRequest) { investigate(); onInvestigationHandled?.(); } }, [investigateRequest]);
  return <div className="wi-detail-grid" id="water-day-details">
    <section className="panel wi-analysis" data-testid="water-analysis">
      <div className="wi-panel-heading"><span className="panel-icon wi-ai-icon"><Sparkles size={18}/></span><div><h3>EcoPulse AI Water Analysis</h3><p>{waterDate(day.date)}</p></div></div>
      {day.is_simulation && <DemoBadge/>}
      <div className="wi-confirmed-facts"><h4><CheckCircle2 size={15}/>Calculated consumption facts</h4><dl><div><dt>{day.is_estimated ? 'Estimated consumption' : day.is_simulation ? 'Simulated consumption' : 'Recorded consumption'}</dt><dd>{waterNumber(day.litres, 1)} L</dd></div><div><dt>Daily target</dt><dd>{waterNumber(day.target_litres)} L</dd></div><div><dt>Difference from target</dt><dd>{day.difference_litres != null && day.difference_litres > 0 ? '+' : ''}{waterNumber(day.difference_litres, 1)} L ({day.percentage_vs_target != null && day.percentage_vs_target > 0 ? '+' : ''}{waterNumber(day.percentage_vs_target, 1)}%)</dd></div><div><dt>Data source</dt><dd>{day.is_estimated ? 'Estimated from meter interval' : day.is_simulation ? 'Simulation' : day.data_source === 'manual' ? 'Manual daily entry' : 'Meter reading'}</dd></div></dl>{day.is_estimated && <p className="wi-help-text">This day's use is allocated from a longer reading interval. It is not a directly measured daily value.</p>}</div>
      {insightQuery.isPending && <p className="wi-help-text" role="status">Looking for saved analysis…</p>}{insightQuery.isError && <p className="form-error" role="alert">Saved analysis unavailable: {insightQuery.error.message}</p>}
      {insight ? <div className="wi-insight-copy"><span className={`wi-source ${insight.source === 'gemini' ? 'gemini' : 'rules'}`}>{insight.source === 'gemini' ? 'Gemini analysis' : 'Rule-based guidance · Gemini unavailable or disabled'}</span><p>{insight.analysis.summary}</p><h4>Possible causes, requiring investigation</h4>{insight.analysis.possible_causes.length ? <ul>{insight.analysis.possible_causes.map((cause, index) => <li key={index}>{cause}</li>)}</ul> : <p>No specific cause can be determined from the available information.</p>}<p className="wi-caveat"><AlertTriangle size={15}/>{insight.analysis.caveat}</p><small>Saved {new Date(insight.generated_at).toLocaleString('en-ZA')}</small></div> : <p className="wi-help-text">Analyse this day to understand patterns and practical next steps. All figures are calculated by the backend before analysis.</p>}
      <div className="wi-actions"><button className="button-primary" onClick={() => analyse.mutate()} disabled={pending || day.litres == null}><Sparkles size={15}/>{analyse.isPending ? 'Analysing…' : 'Analyse with Gemini'}</button><button className="button-outline" data-testid="water-investigate" onClick={investigate} disabled={day.litres == null}><ArrowRight size={15}/>{day.is_simulation ? 'Preview leak investigation' : 'Investigate possible leak'}</button></div>
      {analyse.isError && <p className="form-error" role="alert">{analyse.error.message}</p>}
      <p className="wi-help-text">Unusual consumption suggests something to check. Consumption alone cannot confirm a leak.</p>
      {preview && <div className="wi-preview" data-testid="water-investigation-preview"><button type="button" className="wi-close-preview" aria-label="Close investigation preview" onClick={() => setPreview(false)}><X size={16}/></button><DemoBadge/><h4>Leak investigation preview</h4><p>A real investigation opens your existing incident report, where you can add a location, upload a pipe photo for YOLO analysis and review your report before submitting.</p><p>This simulation stays in preview. It does not create an incident or dispatch maintenance.</p><ol><li>Review the increase and explain household activities.</li><li>Inspect taps, toilets and visible pipes safely.</li><li>Use the existing reporting process if you find suspected damage.</li></ol></div>}
    </section>
    <div className="wi-details-side">
      <section className="panel wi-explanation"><div className="wi-panel-heading"><span className="panel-icon water-icon"><MessageSquareText size={18}/></span><div><h3>Why was your water consumption higher today?</h3><p>Add context for {waterDate(day.date)}. You can update this later.</p></div></div>{day.is_simulation && <DemoBadge/>}
        <form data-testid="water-explanation-form" onSubmit={event => { event.preventDefault(); explanation.mutate(); }}>
          <fieldset disabled={pending || insightQuery.isPending}><legend>Select all activities that apply</legend><div className="wi-reason-grid">{waterReasons.map(([value, label]) => <label key={value} className={`wi-reason ${reasons.includes(value) ? 'selected' : ''}`}><input type="checkbox" checked={reasons.includes(value)} onChange={event => setReasons(previous => event.target.checked ? [...previous, value] : previous.filter(reason => reason !== value))}/>{label}</label>)}</div><label className="wi-text-label">Tell EcoPulse AI what happened today<textarea ref={textRef} rows={3} maxLength={2000} value={text} onChange={event => setText(event.target.value)} placeholder="e.g. We had visitors and did several loads of laundry."/></label></fieldset>
          <p className="wi-help-text">Your explanation provides context; it does not prove that all additional use is accounted for.</p><button className="button-primary" disabled={pending || insightQuery.isPending || (!reasons.length && !text.trim())}><Sparkles size={15}/>{explanation.isPending ? 'Saving and analysing…' : 'Analyse My Explanation'}</button>
          {explanation.isError && <p className="form-error" role="alert">{explanation.error.message}</p>}{explanation.isSuccess && <p className="form-success" role="status">Explanation saved and analysis updated.</p>}
        </form>
      </section>
      <section className="panel wi-recommendations"><div className="wi-panel-heading"><span className="panel-icon wi-saving-icon"><Lightbulb size={18}/></span><div><h3>AI Water Saving Recommendations</h3><p>Practical steps based on the selected day.</p></div></div>{day.is_simulation && <DemoBadge/>}{insight?.analysis.recommendations.length ? <ul>{insight.analysis.recommendations.map((recommendation, index) => <li key={index}>{recommendation}</li>)}</ul> : <p className="wi-help-text">Analyse your consumption to receive recommendations. No potential savings are assumed without enough evidence.</p>}</section>
    </div>
  </div>;
}

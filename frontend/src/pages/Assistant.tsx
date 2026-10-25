import { useState, type FormEvent } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Sparkles, Send, Plus } from 'lucide-react';
import { postJson } from '../services/api';
import './assistant.css';

type Turn = { role: 'user' | 'model'; text: string };
export function Assistant() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState('');
  const send = useMutation({
    mutationFn: (message: string) => postJson<{ answer: string }>('/assistant/chat', { message, history: turns.slice(-10) }),
    onSuccess: (response, message) => {
      setTurns(previous => [...previous, { role: 'user', text: message }, { role: 'model', text: response.answer }]);
      setQuestion('');
    },
  });
  function submit(event: FormEvent) { event.preventDefault(); if (question.trim() && !send.isPending) send.mutate(question.trim()); }
  return <div className="content assistant-page"><header className="page-heading"><div><p className="eyebrow">POWERED BY GEMINI</p><h1>Your community assistant</h1><p className="muted">Practical answers for your home and community.</p></div><button className="button-outline" disabled={send.isPending} onClick={() => { setTurns([]); setQuestion(''); send.reset(); }}><Plus size={16}/> New conversation</button></header>
    <section className="panel assistant-conversation"><div className="assistant-intro"><Sparkles size={25}/><h2>How can I help today?</h2><p>Ask about saving resources, reporting issues, or finding your way around EcoPulse.</p></div>
      {!turns.length && <div className="assistant-prompts">{['How can I reduce my water use?', 'How do I report a burst pipe?', 'What can I do to save electricity?'].map(text => <button key={text} disabled={send.isPending} onClick={() => setQuestion(text)}>{text}</button>)}</div>}
      <div className="assistant-messages" role="log" aria-live="polite">{turns.map((turn, i) => <article key={i} className={`assistant-message ${turn.role}`}><strong>{turn.role === 'user' ? 'You' : 'EcoPulse assistant'}</strong><p>{turn.text}</p></article>)}{send.isPending && <p role="status">Gemini is preparing your answer...</p>}</div>
      {send.isError && <p className="form-error" role="alert">{(send.error as Error).message}</p>}
      <form className="assistant-composer" onSubmit={submit}><label htmlFor="assistant-question">Your question</label><textarea id="assistant-question" value={question} maxLength={2000} required rows={3} disabled={send.isPending} onChange={event => setQuestion(event.target.value)} placeholder="What would you like to know?"/><div><small>{question.length} / 2,000</small><button className="button-primary" disabled={!question.trim() || send.isPending}><Send size={16}/>{send.isPending ? 'Asking Gemini...' : 'Send question'}</button></div></form>
      <p className="assistant-note">Your question and recent conversation are sent to Gemini. Answers may be inaccurate. Check municipal service alerts for live updates; this assistant cannot submit reports.</p>
    </section></div>;
}

// SPDX-FileCopyrightText: Copyright (c) 2026 D1 project contributors
// SPDX-License-Identifier: Apache-2.0
import React, { useEffect, useRef, useState } from 'react';
import { IconArrowUp } from '@tabler/icons-react';
import styles from './D1AgentPanel.module.css';

type Turn = { id: string; event_id: string; question: string; answer?: string; created_at: number; clip_sha256: string;
  status?: 'queued' | 'running' | 'complete' | 'failed'; error?: { code: string; message: string } };
type Phase = 'sending' | 'queued' | 'running';
const pendingLabel = (phase: Phase) => phase === 'queued'
  ? 'Queued — Cosmos will answer after its current review.'
  : phase === 'running' ? 'Cosmos is reviewing the selected clip…' : 'Sending your question…';

async function questionRequest(url: string, signal: AbortSignal, options: RequestInit = {}) {
  // Every HTTP request is short. Waiting for model capacity happens through
  // persisted job status, not a browser connection held open during inference.
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) controller.abort();
  const timeout = setTimeout(abort, 12000);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal, cache: 'no-store' });
    const body = await response.json();
    if (!response.ok) throw new Error(typeof body.error === 'string' ? body.error : 'Cosmos could not accept this question. Try again.');
    return body;
  } finally { clearTimeout(timeout); signal.removeEventListener('abort', abort); }
}

function nextPoll(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const abort = () => { clearTimeout(timer); signal.removeEventListener('abort', abort); reject(new Error('Question polling stopped.')); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, 1000);
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) abort();
  });
}
type Props = {
  eventId: string;
  clipReady: boolean;
  clipTime: string;
  onPendingChange?: (eventId: string, pending: boolean) => void;
  busyElsewhere?: boolean;
  active?: boolean;
  focusRequest?: number;
};

export default function EventQuestions({ eventId, clipReady, clipTime, onPendingChange, busyElsewhere = false, active = true, focusRequest = 0 }: Props) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState('');
  const [pending, setPending] = useState('');
  const [phase, setPhase] = useState<Phase>('sending');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const input = useRef<HTMLTextAreaElement>(null);
  const history = useRef<HTMLDivElement>(null);
  const followBottom = useRef(true);
  const mounted = useRef(true);
  const generation = useRef(0);
  const pendingCallback = useRef(onPendingChange);
  pendingCallback.current = onPendingChange;
  const request = useRef<{ question: string; id: string } | null>(null);
  const postController = useRef<AbortController | null>(null);
  const url = `/api/proxy/d1/reviews/${encodeURIComponent(eventId)}/questions`;

  useEffect(() => {
    mounted.current = true;
    generation.current += 1;
    const currentGeneration = generation.current;
    const controller = new AbortController();
    setTurns([]); setQuestion(''); setPending(''); setError(''); setLoading(true);
    request.current = null;
    async function load() {
      try {
        const result = await questionRequest(url, controller.signal);
        if (!Array.isArray(result)) throw new Error('Saved questions could not be loaded.');
        if (controller.signal.aborted || generation.current !== currentGeneration) return;
        const saved: Turn[] = result.filter((turn: Turn) => turn.event_id === eventId && typeof turn.question === 'string' && /^[a-zA-Z0-9_-]{1,100}$/.test(turn.id));
        setTurns(saved); setLoading(false);
        // Reloading or reattaching only resumes status checks; it never submits
        // another question. Multiple persisted jobs retain their original IDs.
        for (const job of saved.filter(turn => turn.status === 'queued' || turn.status === 'running')) {
          if (controller.signal.aborted || generation.current !== currentGeneration) break;
          if (await answerQuestion(job.question, job.id, currentGeneration, job) === 'interrupted') break;
        }
      } catch (err) {
        if (!controller.signal.aborted && generation.current === currentGeneration) setError(err instanceof Error ? err.message : 'Questions are unavailable.');
      } finally {
        if (!controller.signal.aborted && generation.current === currentGeneration) setLoading(false);
      }
    }
    void load();
    return () => {
      mounted.current = false;
      generation.current += 1;
      controller.abort();
      postController.current?.abort();
      postController.current = null;
      pendingCallback.current?.(eventId, false);
    };
  }, [url, eventId]);

  useEffect(() => {
    if (active && history.current) {
      history.current.scrollTop = history.current.scrollHeight;
      followBottom.current = true;
    }
  }, [active, turns, pending, error]);
  useEffect(() => {
    const node = history.current;
    if (!active || !node || typeof ResizeObserver === 'undefined') return;
    let observing = true;
    const observer = new ResizeObserver(() => {
      // Remember the position before resizing: shrinking the viewport moves its
      // bottom away from the newest answer without a user scroll.
      if (observing && followBottom.current) node.scrollTop = node.scrollHeight;
    });
    observer.observe(node);
    return () => { observing = false; observer.disconnect(); };
  }, [active]);
  useEffect(() => {
    if (active && focusRequest > 0) input.current?.focus();
  }, [active, focusRequest]);

  const answerQuestion = async (text: string, id: string, currentGeneration: number, initial?: Turn) => {
    const controller = new AbortController();
    postController.current = controller;
    request.current = { question: text, id };
    setQuestion(text); setPending(text); setPhase(initial?.status === 'running' ? 'running' : initial ? 'queued' : 'sending'); setError('');
    setTurns(old => old.filter(turn => turn.id !== id));
    pendingCallback.current?.(eventId, true);
    const isCurrent = () => mounted.current && generation.current === currentGeneration && postController.current === controller;
    let accepted = Boolean(initial);
    let terminalFailure = false;
    try {
      let turn: Turn = initial ?? await questionRequest(url, controller.signal, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: text, request_id: id }) });
      while (isCurrent()) {
        if (turn.event_id !== eventId || turn.id !== id || turn.question !== text) throw new Error('The answer did not match this clip. Please try again.');
        if (turn.status === 'failed') {
          terminalFailure = true;
          request.current = null;
          setTurns(old => [...old.filter(item => item.id !== id), turn]);
          throw new Error(`${turn.error?.message || 'Cosmos could not answer this question.'} Retry to submit a new attempt.`);
        }
        if (turn.status === 'queued' || turn.status === 'running') {
          accepted = true; setPhase(turn.status);
          await nextPoll(controller.signal);
          turn = await questionRequest(`${url}/${encodeURIComponent(id)}`, controller.signal);
          continue;
        }
        if ((turn.status && turn.status !== 'complete') || typeof turn.answer !== 'string' || !turn.answer.trim()) throw new Error('The answer did not match this clip. Please try again.');
        setTurns(old => [...old.filter(item => item.id !== turn.id), turn]);
        setQuestion(''); request.current = null;
        return 'complete';
      }
    } catch (err) {
      if (isCurrent()) {
        const message = err instanceof Error && err.name !== 'AbortError' ? err.message : 'Connection interrupted.';
        setError(accepted && !terminalFailure ? `${message} Retry to check the same queued question; it will not be submitted twice.` : message);
      }
      return terminalFailure ? 'failed' : 'interrupted';
    } finally {
      if (isCurrent()) {
        postController.current = null;
        setPending('');
        pendingCallback.current?.(eventId, false);
      }
    }
    return 'interrupted';
  };
  const ask = async (event: React.FormEvent) => {
    event.preventDefault();
    const text = question.trim();
    if (!text || pending || busyElsewhere || !clipReady || loading) return;
    const id = request.current?.question === text ? request.current.id : crypto.randomUUID();
    await answerQuestion(text, id, generation.current);
  };
  const suggest = (text: string) => { setQuestion(text); input.current?.focus(); };
  return <section className={styles.questions} aria-label="Chat about attached clip">
    <div ref={history} data-chat-conversation className={styles.conversation} aria-live="polite" aria-busy={Boolean(pending)} onScroll={event => {
      const node = event.currentTarget;
      followBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight <= 32;
    }}>
      {loading && <p className={styles.muted}>Loading saved questions…</p>}
      {!loading && !turns.length && !pending && <div className={styles.questionWelcome}><p>Ask about what is visible in the attached clip.</p><div className={styles.suggestions}><button disabled={!clipReady} onClick={() => suggest('Is a collision visible in this clip?')}>Is a collision visible?</button><button disabled={!clipReady} onClick={() => suggest('Which lanes are still moving?')}>Which lanes are moving?</button></div></div>}
      {turns.map(turn => <article key={turn.id} className={styles.questionTurn}><div><span>You</span><p>{turn.question}</p></div><div><span>Cosmos · this clip</span><p>{turn.status === 'queued' || turn.status === 'running' ? pendingLabel(turn.status) : turn.status === 'failed' ? turn.error?.message || 'This question was not answered.' : turn.answer}</p>{turn.status === 'failed' && <div className={styles.suggestions}><button type="button" disabled={Boolean(pending)} onClick={() => { request.current = null; suggest(turn.question); }}>Use question again</button></div>}</div></article>)}
      {pending && <article className={styles.questionTurn}><div><span>You</span><p>{pending}</p></div><p className={styles.answering} role="status">{pendingLabel(phase)}</p></article>}
      {busyElsewhere && !pending && <p className={styles.answering} role="status">Another clip has a pending question. You can keep writing here.</p>}
      {error && <p className={styles.questionError} role="alert">{error}</p>}
    </div>
    <form data-chat-composer className={styles.questionForm} onSubmit={ask}><label htmlFor={`question-${eventId}`} className={styles.srOnly}>Question about attached clip</label><textarea ref={input} id={`question-${eventId}`} value={question} maxLength={1200} rows={2} disabled={!clipReady || Boolean(pending)} onChange={event => setQuestion(event.target.value)} placeholder={clipReady ? 'Ask about the attached clip…' : 'Questions available when the clip is ready'} /><button type="submit" aria-label={error && question.trim() ? 'Retry question' : 'Send question'} disabled={!clipReady || !question.trim() || Boolean(pending) || busyElsewhere || loading}><IconArrowUp size={18} aria-hidden="true" /></button></form>
    <p data-chat-scope className={styles.questionScope}>Saved clip · {clipTime}. Off-camera events may be unknown.</p>
  </section>;
}

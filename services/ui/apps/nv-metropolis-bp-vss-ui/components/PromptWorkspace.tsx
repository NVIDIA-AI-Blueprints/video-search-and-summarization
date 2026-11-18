// SPDX-FileCopyrightText: Copyright (c) 2026 D1 project contributors
// SPDX-License-Identifier: Apache-2.0
import React, { useEffect, useId, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import styles from './PromptWorkspace.module.css';

type RecordConfig = { id: string; revision: number; config: Record<string, any> };
type Message = { role: 'user' | 'assistant'; content: string };
type Draft = { question: { type: 'score' | 'choice' | 'noul'; instructions: string; criteria: string[] | Record<string, string> | null }; interval_seconds: number; review: Record<string, any> };
type Base = { stream_id: string; rule_id: string; stream_revision: number; rule_revision: number };
const STORAGE = 'd1-prompt-workspace-v1';

function Field({ label, quiet = false, children }: { label: string; quiet?: boolean; children: React.ReactNode }) {
  const id = useId();
  return <div className={styles.field}><label htmlFor={id} className={quiet ? styles.visuallyHidden : undefined}>{label}</label>{React.isValidElement(children) ? React.cloneElement(children as React.ReactElement<{ id?: string }>, { id }) : children}</div>;
}

export default function PromptWorkspace({ streams, rules, streamId, sourceLabel, ready, onApplied }: { streams: RecordConfig[]; rules: RecordConfig[]; streamId?: string; sourceLabel?: string; ready: boolean; onApplied: () => void }) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [base, setBase] = useState<Base | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState<'chat' | 'apply' | null>(null);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [dirty, setDirty] = useState(false);
  const [choiceText, setChoiceText] = useState('');
  const [promptsOpen, setPromptsOpen] = useState(false);
  const conversationRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const draftFormId = useId();
  const editorId = useId();
  const conversationId = useId();
  const conversationTabId = useId();
  const promptTabId = useId();
  const chatInputId = useId();
  const activeStream = streams.find(s => s.id === streamId) || streams.find(s => s.config.enabled) || streams[0];
  const matchingRules = rules.filter(r => r.config.stream_id === activeStream?.id);
  const activeRule = matchingRules.find(r => r.config.enabled) || matchingRules[0];
  const sourceChanged = !!base && base.stream_id !== activeStream?.id;
  const install = (value: Draft) => { setDraft(value); setChoiceText(value.question.type === 'choice' ? JSON.stringify(value.question.criteria, null, 2) : ''); };
  const loadLive = (announce = true) => {
    if (!activeStream || !activeRule) return;
    const { stream_id, config_revision, enabled, ...review } = activeRule.config;
    install({ question: structuredClone(activeStream.config.question), interval_seconds: activeStream.config.interval_seconds, review });
    setBase({ stream_id: activeStream.id, rule_id: activeRule.id, stream_revision: activeStream.revision, rule_revision: activeRule.revision });
    if (sourceChanged) { setMessages([]); setText(''); }
    setDirty(false); setNotice(announce ? 'Active settings loaded.' : ''); setError('');
  };

  useEffect(() => {
    // Polling may refresh a clean workspace, but must not overwrite an edit,
    // an in-flight request, or a newer revision just returned by Apply.
    if (!activeStream || !activeRule || busy || (base && dirty)) return;
    const sameSource = base?.stream_id === activeStream.id;
    const samePair = sameSource && base?.rule_id === activeRule.id;
    if (samePair) {
      if (activeStream.revision < base.stream_revision || activeRule.revision < base.rule_revision) return;
      if (activeStream.revision === base.stream_revision && activeRule.revision === base.rule_revision) return;
      loadLive(false);
      return;
    }
    // A typed, unsent request still belongs to its original source.
    if (base && text.trim()) return;
    let saved: any;
    try {
      const legacyRaw = sessionStorage.getItem(STORAGE); const legacy = legacyRaw && JSON.parse(legacyRaw);
      if (legacy?.base?.stream_id && !sessionStorage.getItem(`${STORAGE}:${legacy.base.stream_id}`)) {
        sessionStorage.setItem(`${STORAGE}:${legacy.base.stream_id}`, legacyRaw!);
      }
      const raw = sessionStorage.getItem(`${STORAGE}:${activeStream.id}`); saved = raw && JSON.parse(raw);
      if (saved?.draft?.question && saved?.draft?.review && saved?.base?.stream_id === activeStream.id && saved.base.rule_id === activeRule.id
        && (saved.dirty !== false || (saved.base.stream_revision === activeStream.revision && saved.base.rule_revision === activeRule.revision))) {
        install(saved.draft);
        if (typeof saved.choiceText === 'string') setChoiceText(saved.choiceText);
        setBase(saved.base); setMessages(saved.messages || []); setDirty(saved.dirty ?? true);
        setPromptsOpen(saved.dirty !== false); setNotice(''); setError('');
        return;
      }
    } catch { /* Invalid local drafts are replaced with live settings. */ }
    loadLive(false); setMessages(saved?.messages || []);
  }, [streams, rules, streamId, base, dirty, busy, text]);

  useEffect(() => {
    if (draft && base) try { sessionStorage.setItem(`${STORAGE}:${base.stream_id}`, JSON.stringify({ draft, base, messages, dirty, choiceText })); } catch { /* Storage can be disabled. */ }
  }, [draft, base, messages, dirty, choiceText]);
  useEffect(() => {
    // Scroll only the conversation, leaving the video and page position alone.
    const conversation = conversationRef.current;
    if (conversation) conversation.scrollTop = conversation.scrollHeight;
  }, [messages, busy]);
  const edit = (fn: (value: Draft) => Draft) => { setDraft(d => d && fn(d)); setDirty(true); setNotice(''); };
  const reviewEdit = (key: string, value: unknown) => edit(d => ({ ...d, review: { ...d.review, [key]: value } }));
  const materialize = () => {
    if (!draft) throw new Error('Load a stream first.');
    const value = structuredClone(draft);
    value.question.criteria = value.question.type === 'choice' ? JSON.parse(choiceText) : value.question.type === 'noul' ? null : value.question.criteria;
    value.review.value = value.review.field === 'choice' ? String(value.review.value) : Number(value.review.value);
    value.review.probability_key = value.review.field === 'probability' ? value.review.probability_key : null;
    return value;
  };
  const post = async (action: string, body: unknown) => {
    const response = await fetch(`/api/d1/assistant/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const value = await response.json(); if (!response.ok) throw new Error(value.error || 'Request failed'); return value;
  };
  const send = async (event: React.FormEvent) => {
    event.preventDefault(); if (!text.trim() || busy || sourceChanged || !ready) return;
    setBusy('chat'); setPromptsOpen(false); setError(''); setNotice('');
    const conversation: Message[] = [...messages, { role: 'user', content: text.trim() }];
    setMessages(conversation); setText('');
    try {
      let history = conversation.slice(-10);
      while (history.length > 1 && history.reduce((n, m) => n + m.content.length, 0) > 9000) history = history.slice(1);
      const result = await post('draft', { messages: history, current: materialize(), stream_id: base?.stream_id });
      setMessages([...conversation, { role: 'assistant', content: result.reply }]);
      if (result.draft) { install(result.draft); setDirty(true); setPromptsOpen(true); setNotice('Draft ready. Review the prompts, then apply both.'); }
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not generate a draft.'); }
    finally { setBusy(null); }
  };
  const apply = async (event: React.FormEvent) => {
    event.preventDefault(); if (!base || busy || sourceChanged || !dirty) return;
    setBusy('apply'); setError(''); setNotice('');
    try {
      const current = materialize(); const result = await post('apply', { ...base, draft: current });
      install(current); setBase({ ...base, stream_revision: result.stream.revision, rule_revision: result.rule.revision });
      setDirty(false); setNotice('Applied to D1 and Cosmos.'); onApplied();
    } catch (err) { setError(err instanceof Error ? err.message : 'Apply failed.'); }
    finally { setBusy(null); }
  };
  const numeric = (key: string, label: string, min: number, max: number, step = 1) => <Field label={label}><input className={styles.input} type="number" required min={min} max={max} step={step} value={draft?.review[key] ?? ''} onChange={e => reviewEdit(key, e.target.value === '' ? '' : Number(e.target.value))} /></Field>;
  if (!draft || !base) return <section className={styles.workspace} aria-label="Demo assistant"><header className={styles.header}><h3>Assistant</h3></header><p className={styles.waiting} role="status">Waiting for a configured D1 stream and Cosmos rule…</p></section>;
  const examples = base.stream_id.startsWith('traffic') ? ['Alert on stopped or crawling traffic', 'Have Cosmos compare the lanes'] : ['Alert on sustained activity', 'Have Cosmos describe scene changes'];

  return <section className={styles.workspace} aria-label="Demo assistant" data-testid="prompt-workspace">
    <header className={styles.header}>
      <div className={styles.headingRow}><h3>Assistant</h3><span className={ready ? styles.ready : styles.starting}>{ready ? 'Ready' : 'Starting'}</span></div>
      <p className={styles.model}>Nemotron · prompt configuration</p>
      <p className={styles.source} title={sourceChanged ? 'Draft for previous source' : sourceLabel}>For {sourceChanged ? 'the previous source' : sourceLabel || 'the current camera'}</p>
    </header>
    <div className={styles.tabs} role="tablist" aria-label="Assistant workspace" onKeyDown={event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? false : event.key === 'End' ? true : !promptsOpen;
      setPromptsOpen(next);
      document.getElementById(next ? promptTabId : conversationTabId)?.focus();
    }}>
      <button type="button" role="tab" id={conversationTabId} aria-controls={conversationId} aria-selected={!promptsOpen} tabIndex={promptsOpen ? -1 : 0} onClick={() => setPromptsOpen(false)}>Conversation</button>
      <button type="button" role="tab" id={promptTabId} aria-controls={editorId} aria-selected={promptsOpen} tabIndex={promptsOpen ? 0 : -1} onClick={() => setPromptsOpen(true)}>Review prompts{dirty && <span className={styles.draftMark} aria-label="Unapplied draft">*</span>}</button>
    </div>
    <div className={styles.body}>
      <div id={conversationId} role="tabpanel" aria-labelledby={conversationTabId} hidden={promptsOpen} className={styles.conversationPanel}>
        <div ref={conversationRef} role="log" aria-label="Chat messages" className={styles.conversation}>
          {!messages.length && <div className={styles.welcome}>
            <p>Describe what you want to monitor to draft the D1 and Cosmos prompts.</p>
            <div className={styles.examples}>{examples.map(example => <button type="button" key={example} disabled={!!busy || sourceChanged} onClick={() => { setText(example); composerRef.current?.focus(); }}>{example}</button>)}</div>
          </div>}
          {messages.map((message, index) => <div key={index} className={`${styles.message} ${message.role === 'user' ? styles.userMessage : styles.assistantMessage}`}><p className={styles.speaker}>{message.role === 'user' ? 'You' : 'Nemotron'}</p><p>{message.content}</p></div>)}
          {busy === 'chat' && <div className={styles.drafting} role="status"><span>Drafting prompts…</span></div>}
        </div>
      </div>
      <div id={editorId} role="tabpanel" aria-labelledby={promptTabId} hidden={!promptsOpen} className={styles.editor}>
        <p className={styles.editorIntro}>{sourceChanged ? 'Prompts for the previous source.' : dirty ? 'Review this draft before applying it.' : 'These prompts are currently active.'}</p>
          <form id={draftFormId} onSubmit={apply} onInvalidCapture={event => { flushSync(() => setPromptsOpen(true)); const details = (event.target as HTMLElement).closest('details'); if (details) details.open = true; }} aria-label="D1 and Cosmos prompts">
            <fieldset disabled={!!busy || sourceChanged} className={styles.fields}>
              <section className={styles.promptPane} aria-label="D1 prompt pane"><h4><span>D1</span>What to look for</h4><Field label="D1 prompt" quiet><textarea className={styles.input} rows={3} required maxLength={4000} value={draft.question.instructions} onChange={e => edit(d => ({ ...d, question: { ...d.question, instructions: e.target.value } }))} /></Field></section>
              <section className={styles.promptPane} aria-label="Cosmos prompt pane"><h4><span>Cosmos</span>What to explain</h4><Field label="Cosmos prompt" quiet><textarea className={styles.input} required rows={4} maxLength={4000} value={draft.review.review_prompt} onChange={e => reviewEdit('review_prompt', e.target.value)} /></Field></section>
              <details className={styles.advanced}><summary>Advanced controls</summary><div className={styles.advancedFields}>
                <Field label="Check every (seconds)"><input className={styles.input} required type="number" min={0.2} max={60} step={0.1} value={draft.interval_seconds} onChange={e => edit(d => ({ ...d, interval_seconds: Number(e.target.value) }))} /></Field>
                <Field label="D1 answer type"><select className={styles.input} value={draft.question.type} onChange={e => { const type = e.target.value as Draft['question']['type']; edit(d => ({ ...d, question: { ...d.question, type, criteria: type === 'score' ? ['Very busy', 'Busy', 'Not Busy', 'Empty'] : type === 'choice' ? { yes: 'Yes', no: 'No' } : null } })); setChoiceText('{"yes":"Yes","no":"No"}'); }}><option value="score">Ordered labels</option><option value="noul">Yes / no probability</option><option value="choice">Named choices</option></select></Field>
                {draft.question.type === 'score' && <><Field label="Ordered labels (one per line)"><textarea className={styles.input} rows={4} required value={(draft.question.criteria as string[]).join('\n')} onChange={e => edit(d => ({ ...d, question: { ...d.question, criteria: e.target.value.split('\n') } }))} /></Field><p className={styles.help}>Scores start at 0 for the first label. The trigger must use this order.</p></>}
                {draft.question.type === 'choice' && <Field label="Choice keys and labels (JSON)"><textarea className={styles.input} rows={4} required value={choiceText} onChange={e => { setChoiceText(e.target.value); setDirty(true); setNotice(''); }} /></Field>}
                <div className={styles.controlGrid}>
                  <Field label="Trigger field"><select className={styles.input} value={draft.review.field} onChange={e => reviewEdit('field', e.target.value)}>{['score', 'noul', 'choice', 'probability', 'confidence'].map(value => <option key={value}>{value}</option>)}</select></Field>
                  <Field label="Comparison"><select className={styles.input} value={draft.review.operator} onChange={e => reviewEdit('operator', e.target.value)}><option value="lte">≤ threshold</option><option value="gte">≥ threshold</option><option value="eq">= value</option></select></Field>
                  <Field label="Threshold / choice key"><input className={styles.input} required value={draft.review.value} onChange={e => reviewEdit('value', e.target.value)} /></Field>
                  {numeric('consecutive_samples', 'Checks in a row', 1, 100)}
                  {numeric('pre_seconds', 'Seconds before', 0, 120, 0.1)}
                  {numeric('post_seconds', 'Seconds after', 0, 120, 0.1)}
                  {numeric('cooldown_seconds', 'Cooldown (seconds)', 0, 86400, 0.1)}
                  {numeric('max_gap_seconds', 'Maximum sample gap (seconds)', 0.1, 600, 0.1)}
                </div>
                {draft.review.field === 'probability' && <Field label="Probability key"><input className={styles.input} required value={draft.review.probability_key || ''} onChange={e => reviewEdit('probability_key', e.target.value)} /></Field>}
                <p className={styles.technical}>Source: {base.stream_id}<br />Rule: {base.rule_id}</p>
              </div></details>
            </fieldset>
          </form>
      </div>
    </div>
    {(sourceChanged || error || notice) && <div className={styles.feedback}>
      {sourceChanged && <p role="status" className={styles.warning}>The camera source changed. Your previous source’s draft is preserved. Load active settings to work with the current source.</p>}
      {error && <p role="alert" className={styles.error}>{error}</p>}
      {notice && <p role="status" className={styles.notice}>{notice}</p>}
    </div>}
    <form onSubmit={send} className={styles.composer} aria-label="Chat with the demo assistant">
      <label className={styles.visuallyHidden} htmlFor={chatInputId}>Describe your monitoring goal</label><textarea ref={composerRef} id={chatInputId} className={styles.input} placeholder="Request a prompt change…" rows={2} maxLength={2000} value={text} disabled={!!busy || sourceChanged} onChange={event => setText(event.target.value)} />
      <div className={styles.composerActions}><button type="button" className={styles.textButton} disabled={!!busy || !messages.length} onClick={() => setMessages([])}>Clear chat</button><button className={styles.sendButton} disabled={!!busy || !text.trim() || !ready || sourceChanged}>{busy === 'chat' ? 'Generating…' : 'Send'}</button></div>
    </form>
    <footer className={styles.footer}>
      <div className={styles.applyRow}><p className={styles.applyLabel}>{dirty ? 'Unapplied changes' : 'No unapplied changes'}</p><button type="submit" form={draftFormId} disabled={!!busy || !dirty || sourceChanged} className={styles.applyButton}>{busy === 'apply' ? 'Applying…' : 'Apply both'}</button></div>
      <button type="button" disabled={!!busy || !activeRule} onClick={() => loadLive()} className={styles.textButton}>Load active settings</button>
    </footer>
  </section>;
}

// SPDX-FileCopyrightText: Copyright (c) 2026 D1 project contributors
// SPDX-License-Identifier: Apache-2.0
import PromptWorkspace from './PromptWorkspace';
import CosmosReviewFeed from './CosmosReviewFeed';
import layout from './D1MonitorLayout.module.css';
import SourcePreview from './SourcePreview';
import { IconSettings, IconX, IconVideo } from '@tabler/icons-react';
import styles from './D1AgentPanel.module.css';
import { matchIncident } from './d1Presentation';
import React, { useCallback, useEffect, useRef, useState } from 'react';

type ConfigRecord = { id: string; revision: number; config: Record<string, any> };
type VideoSource = { id: string; stream_id: string; label: string; available: boolean; kind?: 'stream' | 'live' | 'recorded' };
type VideoSources = { sources: VideoSource[]; active_source: string | null };
type Section = { data?: any; error?: string };
type Snapshot = { fetched_at: string; [key: string]: Section | string };
const fieldClass = 'w-full rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 p-2 text-sm';
const cardClass = 'rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-5';
const buttonClass = 'rounded bg-[#76b900] px-4 py-2 font-medium text-black disabled:opacity-50';
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block text-sm"><span className="mb-1 block font-medium">{label}</span>{children}</label>;
}
function streamLabel(id?: string) {
  return id ? id.replace(/[-_]+/g, ' ').replace(/^./, c => c.toUpperCase()) : 'No stream configured';
}

function ConfigEditor({ kind, records, streams, preferredId, onSaved }: {
  kind: 'streams' | 'rules'; records: ConfigRecord[]; streams: ConfigRecord[]; preferredId?: string; onSaved: () => void;
}) {
  const [selected, setSelected] = useState('');
  const [draft, setDraft] = useState<Record<string, any> | null>(null);
  const [criteria, setCriteria] = useState('');
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  const record = records.find(r => r.id === selected);
  const load = (item: ConfigRecord) => {
    setSelected(item.id); setDraft(structuredClone(item.config));
    setCriteria(JSON.stringify(item.config.question?.criteria ?? null, null, 2)); setMessage('');
  };
  // Polls never overwrite an edit in progress.
  useEffect(() => { if (!draft && records.length) load(records.find(r => r.id === preferredId) || records[0]); }, [records, draft, preferredId]);
  const update = (key: string, value: unknown) => setDraft(d => ({ ...d, [key]: value }));
  const number = (key: string, label: string, min: number, max: number, step: number = 1) => (
    <Field label={label}><input className={fieldClass} type="number" required min={min} max={max} step={step}
      value={draft?.[key] ?? ''} onChange={e => update(key, e.target.value === '' ? '' : Number(e.target.value))} /></Field>
  );
  const save = async (event: React.FormEvent) => {
    event.preventDefault(); if (!draft) return; setSaving(true); setMessage('');
    try {
      const payload = structuredClone(draft);
      if (kind === 'streams') payload.question.criteria = payload.question.type === 'noul' ? null : JSON.parse(criteria);
      if (kind === 'rules') {
        payload.value = payload.field === 'choice' ? String(payload.value) : Number(payload.value);
        payload.probability_key = payload.field === 'probability' ? payload.probability_key : null;
      }
      const response = await fetch(`/api/proxy/d1/${kind}/${encodeURIComponent(selected)}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Save failed');
      setMessage(`Saved ${selected}, revision ${result.revision}.`); onSaved();
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Save failed'); }
    finally { setSaving(false); }
  };
  if (!draft) return <div className={cardClass}>No {kind} configured yet. Register them through the service API.</div>;
  const boundStream = streams.find(s => s.id === draft.stream_id);
  return <form className={cardClass + ' space-y-4'} onSubmit={save}>
    <h3 className="text-lg font-semibold">{kind === 'streams' ? 'D1 inference' : 'Cosmos trigger and clip'}</h3>
    <Field label={kind === 'streams' ? 'Stream' : 'Rule'}><select className={fieldClass} value={selected} onChange={e => load(records.find(r => r.id === e.target.value)!)}>
      {records.map(r => <option key={r.id} value={r.id}>{r.id} · revision {r.revision}</option>)}
    </select></Field>
    <label className="flex items-center gap-2"><input type="checkbox" checked={draft.enabled} onChange={e => update('enabled', e.target.checked)} />Enabled</label>
    {kind === 'streams' ? <>
      <Field label="What should D1 watch for?"><textarea className={fieldClass} required rows={3} maxLength={4000} value={draft.question.instructions}
        onChange={e => update('question', { ...draft.question, instructions: e.target.value })} /></Field>
      {number('interval_seconds', 'Check every (seconds)', 0.2, 60, 0.1)}
      <Field label="What D1 receives"><select className={fieldClass} value={draft.input_mode || 'image'} onChange={e => {
        const mode = e.target.value;
        setDraft(d => ({ ...d, input_mode: mode, video_frames: d?.video_frames ?? 8, video_fps: d?.video_fps ?? 1 }));
      }}><option value="image">One image</option><option value="video">Recent video frames</option></select></Field>
      {draft.input_mode === 'video' && <div className="grid grid-cols-2 gap-3">
        {number('video_frames', 'Frames per video check', 2, 16)}
        {number('video_fps', 'Sampled frames per second', 0.5, 5, 0.5)}
        <p className="col-span-2 text-xs text-gray-500">D1 compares motion across these frames. Video input alone does not establish a measured speed in mph.</p>
      </div>}
      <details className="border-t border-gray-200 dark:border-gray-700 pt-3"><summary className="cursor-pointer text-sm">Source and answer format</summary><div className="space-y-4 mt-4">
        <Field label="RTSP source"><input className={fieldClass} required value={draft.rtsp_url} onChange={e => update('rtsp_url', e.target.value)} /></Field>
        <Field label="Registered VIOS sensor"><input className={fieldClass} value={draft.vios_sensor || ''} onChange={e => update('vios_sensor', e.target.value || null)} /></Field>
        <p className="text-xs text-gray-500">Source changes must also be registered in VIOS for clip recording.</p>
        <Field label="Answer type"><select className={fieldClass} value={draft.question.type} onChange={e => {
          update('question', { ...draft.question, type: e.target.value });
          setCriteria(e.target.value === 'score' ? '["Very busy", "Busy", "Not Busy", "Empty"]' : e.target.value === 'choice' ? '{"yes": "Yes", "no": "No"}' : 'null');
        }}><option value="score">Ordered score</option><option value="choice">Choice</option><option value="noul">Yes/no probability</option></select></Field>
        {draft.question.type !== 'noul' && <Field label="Answer labels (JSON; score order is significant)"><textarea className={fieldClass + ' font-mono'} rows={4} required value={criteria} onChange={e => setCriteria(e.target.value)} /></Field>}
      </div></details>
      <p className="text-xs text-gray-500 dark:text-gray-400">After changing the question, source, or video input, update the rule’s stream revision in Advanced settings.</p>
    </> : <>
      {boundStream && draft.config_revision !== boundStream.revision && <p role="alert" className="text-amber-700 dark:text-amber-400">The D1 question changed. Update the stream revision in Advanced settings to resume matching.</p>}
      <div className="rounded-lg bg-gray-100 dark:bg-gray-900 p-3 text-sm">Review when <strong>{draft.field} {draft.operator === 'lte' ? '≤' : draft.operator === 'gte' ? '≥' : '='} {draft.value}</strong> for <strong>{draft.consecutive_samples} checks</strong>. Wait {draft.cooldown_seconds}s between events.</div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Trigger threshold"><input className={fieldClass} required value={draft.value} onChange={e => update('value', e.target.value)} /></Field>
        {number('consecutive_samples', 'Checks in a row', 1, 100)}
        {number('pre_seconds', 'Seconds before event', 0, 120, 0.1)}
        {number('post_seconds', 'Seconds after event', 0, 120, 0.1)}
      </div>
      <Field label="What should Cosmos explain?"><textarea className={fieldClass} required rows={3} maxLength={4000} value={draft.review_prompt} onChange={e => update('review_prompt', e.target.value)} /></Field>
      <details className="border-t border-gray-200 dark:border-gray-700 pt-3"><summary className="cursor-pointer text-sm">Advanced settings</summary><div className="space-y-4 mt-4">
        <Field label="Stream ID"><select className={fieldClass} value={draft.stream_id} onChange={e => update('stream_id', e.target.value)}>{streams.map(s => <option key={s.id}>{s.id}</option>)}</select></Field>
        {number('config_revision', `Required stream revision (current: ${boundStream?.revision ?? 'unknown'})`, 1, 2147483647)}
        <div className="grid grid-cols-2 gap-3">
          <Field label="Output field"><select className={fieldClass} value={draft.field} onChange={e => update('field', e.target.value)}>{['score', 'noul', 'confidence', 'choice', 'probability'].map(v => <option key={v}>{v}</option>)}</select></Field>
          <Field label="Comparison"><select className={fieldClass} value={draft.operator} onChange={e => update('operator', e.target.value)}><option value="lte">≤ threshold</option><option value="gte">≥ threshold</option><option value="eq">= threshold</option></select></Field>
          {draft.field === 'probability' && <Field label="Probability key"><input className={fieldClass} required value={draft.probability_key || ''} onChange={e => update('probability_key', e.target.value)} /></Field>}
          {number('max_gap_seconds', 'Maximum sample gap (s)', 0.1, 600, 0.1)}
          {number('cooldown_seconds', 'Cooldown (s)', 0, 86400, 0.1)}
        </div>
      </div></details>
    </>}
    <div className="flex flex-wrap gap-3"><button type="submit" className={buttonClass} disabled={saving}>{saving ? 'Saving…' : 'Save ' + (kind === 'streams' ? 'stream' : 'rule')}</button>
      <button type="button" className="rounded border px-3 py-2" disabled={saving || !record} onClick={() => record && load(record)}>Reload saved values</button></div>
    {message && <p role="status" className="break-words text-sm">{message}</p>}
  </form>;
}

function answerLabel(answer: any) {
  if (!answer) return 'Waiting for D1';
  if (answer.choice) return answer.legend?.[answer.choice] || answer.choice;
  if (answer.legend && answer.probabilities) {
    const top = Object.entries(answer.probabilities).sort((a, b) => Number(b[1]) - Number(a[1]))[0];
    return top ? answer.legend[top[0]] : 'Scene scored';
  }
  if (typeof answer.noul !== 'number' || !Number.isFinite(answer.noul) || answer.noul < 0 || answer.noul > 1) return 'Scene scored';
  const yes = answer.noul >= 0.5;
  const confidence = Math.round((yes ? answer.noul : 1 - answer.noul) * 100);
  return `${yes ? 'Yes' : 'No'} (${confidence}% confidence)`;
}
function inputLabel(decision: any) {
  if (!decision) return '';
  const model = typeof decision.model === 'string' ? decision.model.split('/').pop() : 'D1';
  if (decision.input?.mode !== 'video') return `${model} · Single image`;
  const seconds = decision.input.window_end - decision.input.window_start;
  return `${model} · ${decision.input.frame_count} frames${Number.isFinite(seconds) ? ` over ${seconds.toFixed(1)}s` : ''}`;
}
function clockTime(value: number) { return new Date(value * 1000).toLocaleTimeString(); }
function savedTime(value: number) { return new Date(value * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }); }

export default function D1AgentPanel({ isActive }: { isActive: boolean }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState('');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [setupTab, setSetupTab] = useState<'prompts' | 'advanced'>('prompts');
  const settingsDialog = useRef<HTMLDialogElement>(null);
  const [paused, setPaused] = useState(false);
  const [sourceBusy, setSourceBusy] = useState(false);
  const [sourceError, setSourceError] = useState('');
  useEffect(() => {
    const dialog = settingsDialog.current;
    if (!dialog) return;
    if (settingsOpen && !dialog.open) {
      if (typeof dialog.showModal === 'function') dialog.showModal(); else dialog.setAttribute('open', '');
    } else if (!settingsOpen && dialog.open) {
      if (typeof dialog.close === 'function') dialog.close(); else dialog.removeAttribute('open');
    }
  }, [settingsOpen]);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await fetch('/api/proxy/d1/snapshot', { signal });
      if (!response.ok) throw new Error(`Unable to refresh (${response.status})`);
      setSnapshot(await response.json()); setError('');
    } catch (err) { if (!signal?.aborted) setError(err instanceof Error ? err.message : 'Connection failed'); }
  }, []);
  useEffect(() => {
    if (!isActive || paused) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    const poll = async () => { await refresh(controller.signal); if (!controller.signal.aborted) timer = setTimeout(poll, 5000); };
    void poll(); return () => { controller.abort(); clearTimeout(timer); };
  }, [isActive, paused, refresh]);
  const data = (key: string, fallback: any = []) => (snapshot?.[key] as Section)?.data ?? fallback;
  const streams: ConfigRecord[] = data('streams'); const rules: ConfigRecord[] = data('rules');
  const decisions = data('decisions'); const reviews = data('reviews'); const jobs = data('jobs'); const storedEvents = data('events');
  const projection = data('incidents', null);
  const sources: VideoSources = data('sources', { sources: [], active_source: null });
  const assistant = data('assistant', {});
  const showPromptAssistant = assistant.configured ?? assistant.ready === true;
  const activeSetupTab = showPromptAssistant ? setupTab : 'advanced';
  const switchSource = async (nextSource: string) => {
    if (!nextSource || sourceBusy || nextSource === sources.active_source || !sources.sources.some(source => source.id === nextSource && source.available)) return;
    setSourceBusy(true); setSourceError('');
    try {
      const response = await fetch('/api/proxy/d1/demo/sources', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source_id: nextSource }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Source change failed');
      await refresh();
    } catch (err) { setSourceError(err instanceof Error ? err.message : 'Source change failed'); }
    finally { setSourceBusy(false); }
  };
  const activeStream = streams.find(s => s.config.enabled) || streams.find(s => s.id === decisions[0]?.stream_id) || streams[0];
  const activeRules = rules.filter(r => r.config.stream_id === activeStream?.id);
  const activeRule = activeRules.find(r => r.config.enabled) || activeRules[0];
  const latest = decisions.find((decision: any) => decision.stream_id === activeStream?.id);
  const sourceJobs = jobs.filter((job: any) => job.stream_id === activeStream?.id);
  const verifiedIncidents = (projection?.incidents || []).filter((incident: any) => incident.stream_id === activeStream?.id);
  const candidates = (projection?.candidates || []).filter((candidate: any) => candidate.stream_id === activeStream?.id);
  const events = projection ? candidates.map((candidate: any) => {
    const job = sourceJobs.find((item: any) => item.id === candidate.event_id);
    return { ...job, ...candidate.event, ...candidate, id: candidate.event_id, incident: candidate.event,
      review: candidate.review || (candidate.status === 'legacy' ? reviews.find((review: any) => review.event_id === candidate.event_id && (!review.stream_id || review.stream_id === candidate.stream_id) && !review.verification_version) : null), status: candidate.status, clip_start: candidate.event.clip_start, clip_end: candidate.event.clip_end };
  }) : sourceJobs.map((job: any) => ({ ...job, incident: matchIncident(job, storedEvents), review: reviews.find((r: any) => r.event_id === job.id && (!r.stream_id || r.stream_id === job.stream_id)) }));
  // The representative may outlive the bounded candidate list. Keep its exact event and review available.
  for (const incident of verifiedIncidents) {
    if (!events.some((event: any) => event.id === incident.representative_event_id) && incident.event) events.push({
      ...incident.event, id: incident.representative_event_id, status: 'confirmed', clip_ready: true, incident: incident.event, review: incident.review,
    });
  }
  const activeIncidents = verifiedIncidents.filter((incident: any) => incident.status === 'active');
  const fetchedTime = snapshot ? Date.parse(snapshot.fetched_at) / 1000 : 0;
  const decisionAge = latest ? fetchedTime - latest.captured_at : Infinity;
  const fresh = decisionAge <= Math.max(15, (activeStream?.config.interval_seconds || 2) * 4);
  const healthy = !error && !(snapshot?.health as Section)?.error && data('health', {}).ready && fresh;
  const currentAnswer = healthy && activeStream?.config.enabled && !paused;
  const autoReview = activeStream?.config.enabled && activeRules.some(r => r.config.enabled && r.config.config_revision === activeStream.revision);
  const activeSource = sources.sources.find(source => source.stream_id === activeStream?.id);
  const activeSourceLabel = activeSource?.label || streamLabel(activeStream?.id);
  const sourceKind = !activeStream ? 'No source' : activeSource?.kind === 'recorded'
    ? 'Recorded source' : activeSource?.kind === 'live' ? 'Live source' : 'RTSP source';
  return <section className={styles.root} aria-label="D1 stream monitor" data-input-state={healthy && activeStream?.config.enabled && !paused ? 'ready' : 'unavailable'}>
    <header className={styles.header}>
      <div className={styles.titleRow}><h2>Traffic monitor</h2><span className={styles.source}>Source: {activeSourceLabel}</span><span className={styles.sourceBadge}>{sourceKind}</span></div>
      <div className={styles.headerActions}><span className={styles.edgeBadge}>Running on Jetson Thor</span>
        {(!snapshot || paused || !activeStream?.config.enabled || healthy) && <span className={`${styles.health} ${healthy && activeStream?.config.enabled && !paused ? styles.healthy : styles.waiting}`}><span aria-hidden="true">●</span>{!snapshot ? 'Connecting' : paused ? 'Display paused' : !activeStream?.config.enabled ? 'Monitoring paused' : 'Monitoring'}</span>}
        <button className={styles.secondaryButton} onClick={() => setSettingsOpen(true)}><IconSettings size={16} aria-hidden="true" />Monitoring setup</button>
      </div>
    </header>
    <div className={styles.workspace}>
      {sources.sources.length > 0 && <div className={layout.streamControl} aria-busy={sourceBusy}>
        <label htmlFor="demo-source">Stream</label>
        <select id="demo-source" value={sources.active_source || ''} disabled={sourceBusy} onChange={event => void switchSource(event.target.value)}>
          {!sources.active_source && <option value="">Choose a stream</option>}
          {sources.sources.map(source => <option key={source.id} value={source.id} disabled={!source.available}>{source.label}</option>)}
        </select>
        <span role="status">{sourceBusy ? 'Switching stream…' : 'Select a stream to monitor.'}</span>
      </div>}
      {sourceError && <p role="alert" className={styles.sourceSwitchError}>{sourceError}</p>}
      <section className={layout.liveSection} aria-labelledby="d1-live-title">
        <div className={layout.sectionHeading}><h3 id="d1-live-title"><span>1</span>D1 live inference</h3><span>{sourceKind === 'Recorded source' ? 'Recorded input' : sourceKind}</span></div>
        <div className={layout.liveGrid}>
          <div className={layout.inputPane}>
            <div className={layout.paneHeading}>Video going into D1</div>
            <div className={layout.inputVideo}>{activeStream ? <SourcePreview key={`${activeStream.id}:${activeStream.revision}`} streamId={activeStream.id} revision={activeStream.revision} paused={paused || !isActive || !activeStream.config.enabled || sourceBusy} /> : <div className={layout.noInput}><IconVideo size={28} aria-hidden="true" /><p>{snapshot ? 'Choose a video source' : 'Connecting to the monitor…'}</p></div>}</div>
          </div>
          <section className={layout.decisionPane} aria-label="Latest D1 check">
            <div className={layout.paneHeading}>D1 decision model</div>
            <div className={layout.decisionBody}>
              <div className={layout.question}><span>Monitoring question</span><p data-testid="d1-question">{latest ? latest.question?.instructions || 'Question unavailable' : activeStream?.config.question?.instructions || 'No question configured'}</p></div>
              <div className={layout.score}><div><span>{latest && !currentAnswer ? fresh ? 'Last D1 answer' : 'Last D1 answer · out of date' : 'Latest D1 answer'}</span><strong>{answerLabel(latest?.answer)}</strong></div><time>{latest ? currentAnswer ? clockTime(latest.captured_at) : savedTime(latest.captured_at) : 'Waiting for a frame'}</time></div>
              <div className={layout.trigger}><strong>When does Cosmos get a clip?</strong><p>{autoReview ? activeRule?.config.operator === 'gte' && activeRule.config.field === 'noul' ? `Yes with at least ${Math.round(activeRule.config.value * 100)}% confidence for ${activeRule.config.consecutive_samples} consecutive checks.` : 'When the configured D1 trigger rule matches.' : 'Automatic verification is off.'}</p></div>
            </div>
          </section>
        </div>
      </section>
      <section className={layout.cosmosSection} aria-labelledby="cosmos-review-title">
        <div className={layout.sectionHeading}><h3 id="cosmos-review-title"><span>2</span>Cosmos verification</h3><span>Saved clips · newest first · scroll for more</span></div>
        <CosmosReviewFeed key={activeStream?.id || 'no-source'} events={events} incidents={verifiedIncidents} streamRevision={activeStream?.revision} activeRule={activeRule} available={Boolean(projection)} autoReview={Boolean(autoReview)} />
        {projection && events.length > 0 && <p className={layout.feedNote}>{activeIncidents.length} ongoing verified incident{activeIncidents.length === 1 ? '' : 's'} · Repeated candidate clips are grouped into the same incident.</p>}
      </section>
      <details className={styles.architecture}><summary>How VSS connects this demo</summary><p>The camera feeds D1 continuously. VSS VIOS records the video and retrieves the event clip. Cosmos checks that clip through VSS RT-VLM. This demo groups confirmed candidates into incidents and keeps the evidence available for questions.</p><p>Kafka carries decisions, events and reviews; Elasticsearch stores their searchable history. The monitoring and incident rules are this demo’s extension to VSS.</p><p className={styles.decisionMeta}><span>{activeStream?.config.enabled ? `Every ${activeStream.config.interval_seconds}s` : 'Paused'}</span><span data-testid="d1-input">{inputLabel(latest)}</span><span>{autoReview ? 'Automatic verification is on' : 'Automatic verification is off'}</span></p><p>Confidence shows D1’s support for its Yes or No answer. It is not a vehicle speed or the percentage of the road that is congested.</p></details>
      <footer className={styles.footer}><span>{snapshot ? `Updated ${new Date(snapshot.fetched_at).toLocaleTimeString()}` : 'Connecting'} · Model-generated assessments</span><button onClick={() => setPaused(!paused)}>{paused ? 'Resume updates' : 'Pause updates'}</button></footer>
    </div>
    <dialog ref={settingsDialog} className={styles.dialog} aria-labelledby="d1-settings-title" onCancel={() => setSettingsOpen(false)} onClose={() => setSettingsOpen(false)}>
      <header className={styles.dialogHeader}><div><h2 id="d1-settings-title">Monitoring setup</h2><p>Configure what to monitor. These controls do not answer questions about a clip.</p></div><button className={styles.secondaryButton} onClick={() => setSettingsOpen(false)} aria-label="Close settings"><IconX size={20} /></button></header>
      {showPromptAssistant && <>
        <div className={styles.setupTabs} role="tablist" aria-label="Monitoring setup sections"><button role="tab" aria-selected={activeSetupTab === 'prompts'} onClick={() => setSetupTab('prompts')}>Prompt assistant</button><button role="tab" aria-selected={activeSetupTab === 'advanced'} onClick={() => setSetupTab('advanced')}>Source & trigger settings</button></div>
        <div hidden={activeSetupTab !== 'prompts'} className={styles.promptSetup}><PromptWorkspace streams={streams} rules={rules} streamId={activeStream?.id} sourceLabel={activeSourceLabel} ready={assistant.ready === true} onApplied={() => void refresh()} /></div>
      </>}
      {settingsOpen && activeSetupTab === 'advanced' && <div className={styles.settingsGrid}><ConfigEditor kind="streams" records={streams} streams={streams} preferredId={activeStream?.id} onSaved={() => void refresh()} /><ConfigEditor kind="rules" records={rules} streams={streams} preferredId={activeRule?.id} onSaved={() => void refresh()} /></div>}
    </dialog>
  </section>;
}

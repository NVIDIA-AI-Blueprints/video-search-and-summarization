// SPDX-FileCopyrightText: Copyright (c) 2026 D1 project contributors
// SPDX-License-Identifier: Apache-2.0
import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { IconCheck, IconClock, IconMessageCircle, IconPlayerPlay, IconX, IconAlertTriangle, IconArrowUp, IconPaperclip, IconMovie } from '@tabler/icons-react';
import EventQuestions from './EventQuestions';
import { describeEventWindow, describeTrigger, eventTriggerQuestion } from './d1Presentation';
import styles from './CosmosReviewFeed.module.css';

type Props = { events: any[]; incidents: any[]; streamRevision?: number; activeRule?: any; available: boolean; autoReview: boolean };
const savedTime = (value: number) => new Date(value * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit' });
const verdictOf = (event: any) => event.review?.verification_version === 1 && ['confirmed', 'rejected', 'uncertain'].includes(event.review.verdict) ? event.review.verdict as string : null;
function resultLabel(event: any) {
  const verdict = verdictOf(event);
  if (verdict) return ({ confirmed: 'Confirmed', rejected: 'Not confirmed', uncertain: 'Uncertain' } as Record<string, string>)[verdict];
  if (event.status === 'legacy' || event.review) return 'Earlier description · unverified';
  if (event.status === 'failed') return 'Review failed';
  if (event.status === 'reviewing') return 'Reviewing clip';
  return event.attempts > 0 ? 'Retrying review' : 'Waiting for review';
}

function EventClip({ event, root, first }: { event: any; root: React.RefObject<HTMLDivElement | null>; first: boolean }) {
  const holder = useRef<HTMLDivElement>(null);
  const [nearby, setNearby] = useState(first);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (nearby || !holder.current) return;
    if (typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setNearby(true); observer.disconnect(); }
    }, { root: root.current, rootMargin: '80px 0px' });
    observer.observe(holder.current);
    return () => observer.disconnect();
  }, [nearby, root]);
  const waiting = !event.clip_ready && ['pending', 'reviewing'].includes(event.status);
  return <div ref={holder} className={styles.clip}>
    {waiting ? <div className={styles.videoStatus}><IconClock size={24} aria-hidden="true" /><p>{event.attempts > 0 ? 'The event clip is delayed.' : 'Preparing event clip…'}</p></div>
      : failed ? <div className={styles.videoStatus}><p>This saved clip is unavailable.</p><button onClick={() => { setFailed(false); setAttempt(value => value + 1); }}>Retry video</button></div>
      : nearby ? <video key={attempt} controls muted playsInline preload="metadata" aria-label={`Saved evidence clip ${event.id}`}
          src={`/api/proxy/d1/clips/${encodeURIComponent(event.id)}?attempt=${attempt}`}
          onError={() => setFailed(true)} onLoadedMetadata={e => { if (e.currentTarget.currentTime === 0) e.currentTarget.currentTime = 0.01; }} />
        : <button className={styles.loadVideo} onClick={() => setNearby(true)} aria-label={`Load saved clip ${event.id}`}><IconPlayerPlay size={30} aria-hidden="true" /><span>Load event clip</span></button>}
  </div>;
}

function ReviewRow({ event, incident, streamRevision, activeRule, root, first, ask, attached }: {
  event: any; incident?: any; streamRevision?: number; activeRule?: any;
  root: React.RefObject<HTMLDivElement | null>; first: boolean; ask: (event: any) => void; attached: boolean;
}) {
  const verdict = verdictOf(event);
  const unverified = !verdict && Boolean(event.review || event.status === 'legacy');
  const question = eventTriggerQuestion(event.incident);
  const previousSettings = event.incident && (
    streamRevision !== undefined && event.incident.trigger?.config_revision !== undefined && event.incident.trigger.config_revision !== streamRevision
    || activeRule?.id === event.incident.rule_id && event.incident.rule_revision !== undefined && event.incident.rule_revision !== activeRule?.revision);
  const summary = verdict ? event.review.summary : event.review ? event.review.context
    : event.status === 'legacy' ? 'This event predates verification. No verdict was recorded.'
    : event.status === 'failed' ? 'Verification could not be completed. No verdict was issued.'
    : event.attempts > 0 ? 'Verification is delayed. An automatic retry is scheduled.'
    : event.status === 'reviewing' ? 'Cosmos is checking the saved clip.' : 'The saved clip will be sent to Cosmos for verification.';
  return <article className={styles.row} data-event-id={event.id} aria-label={`Saved event ${savedTime(event.captured_at)}`}>
    <div className={styles.evidence}>
      <div className={styles.clipHeading}><strong>Event clip</strong><time dateTime={new Date(event.captured_at * 1000).toISOString()}>{savedTime(event.captured_at)}</time></div>
      <EventClip event={event} root={root} first={first} />
      <div className={styles.clipCaption}><span>Saved recording</span><span>{Math.round(event.clip_end - event.clip_start)}s requested window</span></div>
    </div>
    <div className={styles.result} data-verdict={verdict || (unverified ? 'unverified' : event.status === 'failed' ? 'failed' : 'pending')}>
      <div className={styles.resultHeading}><strong>Cosmos result</strong><span className={styles.verdict}>{verdict === 'confirmed' ? <IconCheck size={16} aria-hidden="true" /> : verdict === 'rejected' || verdict === 'uncertain' || event.status === 'failed' ? <IconAlertTriangle size={16} aria-hidden="true" /> : <IconClock size={16} aria-hidden="true" />}{resultLabel(event)}</span></div>
      <p className={styles.summary}>{summary || 'A description is not available for this saved clip.'}</p>
      {verdict === 'rejected' && <p className={styles.note}>Not added to verified incidents.</p>}
      {verdict === 'uncertain' && <p className={styles.note}>Not added to verified incidents. More evidence is needed.</p>}
      {unverified && <p className={styles.note}>This is a description, not a verification verdict.</p>}
      <details className={styles.details}><summary>Evidence &amp; trigger details</summary>
        {incident?.candidate_count > 1 && <p className={styles.note}>Part of one incident · {incident.candidate_count} candidate clips grouped</p>}
        {verdict && event.review.evidence?.length > 0 && <><h5>Visible evidence</h5><ul>{event.review.evidence.map((item: string, index: number) => <li key={index}>{item}</li>)}</ul></>}
        {verdict && event.review.uncertainty?.length > 0 && <><h5>What the clip cannot establish</h5><ul>{event.review.uncertainty.map((item: string, index: number) => <li key={index}>{item}</li>)}</ul></>}
        <h5>D1’s saved question</h5><p>{question || 'The saved question is not available.'}</p>
        <p>{describeTrigger(event.incident) || 'Trigger details are not available.'}</p>
        {describeEventWindow(event.incident) && <p>{describeEventWindow(event.incident)}</p>}
        {previousSettings && <p>This clip uses earlier monitoring settings.</p>}
        <p>Event: {event.id}</p>{event.review?.clip_sha256 && <p>Reviewed clip SHA-256: {event.review.clip_sha256}</p>}
      </details>
      <button className={styles.askButton} disabled={!event.clip_ready} aria-pressed={attached} onClick={() => ask(event)}>{attached ? <IconCheck size={16} aria-hidden="true" /> : <IconPaperclip size={16} aria-hidden="true" />}{attached ? 'In chat' : 'Add to chat'}</button>
    </div>
  </article>;
}

export default function CosmosReviewFeed({ events, incidents, streamRevision, activeRule, available, autoReview }: Props) {
  const feed = useRef<HTMLDivElement>(null);
  const chat = useRef<HTMLElement>(null);
  const [chatEvents, setChatEvents] = useState<any[]>([]);
  const [attachedId, setAttachedId] = useState<string | null>(null);
  const [focusRequest, setFocusRequest] = useState(0);
  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());
  const qaEvent = chatEvents.find(event => event.id === attachedId);
  const setChatPending = useCallback((id: string, pending: boolean) => {
    setPendingIds(old => {
      if (old.has(id) === pending) return old;
      const next = new Set(old); if (pending) next.add(id); else next.delete(id); return next;
    });
  }, []);
  const [playbackEvent, setPlaybackEvent] = useState<any>(null);
  const [newAbove, setNewAbove] = useState(false);
  const anchor = useRef<{ top: boolean; id?: string; offset: number; event?: any }>({ top: true, offset: 0 });
  // Keep the playing clip, recent chat targets and reading anchor accessible
  // beyond the backend's bounded candidate list. Incoming data takes precedence.
  const retained = [playbackEvent, ...chatEvents, anchor.current.event].filter(Boolean);
  const sorted = [...new Map([...retained, ...events].filter(event => event?.id && Number.isFinite(event.captured_at)).map(event => [event.id, event])).values()]
    .sort((a, b) => b.captured_at - a.captured_at || a.id.localeCompare(b.id));
  const order = sorted.map(event => event.id).join('|');
  const previousOrder = useRef(order);
  useEffect(() => {
    setPlaybackEvent((old: any) => old ? events.find(event => event.id === old.id) || old : old);
    setChatEvents(old => old.map(event => events.find(item => item.id === event.id) || event));
  }, [events]);
  const recordPosition = () => {
    const node = feed.current; if (!node) return;
    const top = node.getBoundingClientRect().top;
    const row = Array.from(node.querySelectorAll<HTMLElement>('[data-event-id]')).find(item => item.getBoundingClientRect().bottom > top + 5);
    const atTop = node.scrollTop < 8;
    anchor.current = { top: atTop, id: row?.dataset.eventId, offset: row ? row.getBoundingClientRect().top - top : 0,
      event: atTop ? undefined : sorted.find(event => event.id === row?.dataset.eventId) };
    if (anchor.current.top) setNewAbove(false);
  };
  useLayoutEffect(() => {
    const node = feed.current; if (!node) return;
    if (anchor.current.top) node.scrollTop = 0;
    else {
      const row = Array.from(node.querySelectorAll<HTMLElement>('[data-event-id]')).find(item => item.dataset.eventId === anchor.current.id);
      if (row) node.scrollTop += row.getBoundingClientRect().top - node.getBoundingClientRect().top - anchor.current.offset;
      if (sorted[0]?.id !== previousOrder.current.split('|')[0]) setNewAbove(true);
    }
    previousOrder.current = order;
    recordPosition();
  });
  const addToChat = (event: any) => {
    if (!event.clip_ready) return;
    setChatEvents(old => {
      const next = [...old.filter(item => item.id !== event.id), event];
      // Preserve drafts, saved answers and in-flight requests for recent clips.
      // A hidden pending thread must never be evicted or retargeted by polling.
      if (next.length > 4) {
        const oldestIdle = next.findIndex(item => item.id !== event.id && !pendingIds.has(item.id));
        if (oldestIdle >= 0) next.splice(oldestIdle, 1);
      }
      return next;
    });
    setAttachedId(event.id);
    setFocusRequest(value => value + 1);
    if (typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 1000px)').matches) {
      chat.current?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
    }
  };
  return <div className={styles.root}>
    <div className={styles.historyPane}>
    {newAbove && <button className={styles.newClips} onClick={() => { feed.current?.scrollTo({ top: 0, behavior: 'smooth' }); setNewAbove(false); }}><IconArrowUp size={14} aria-hidden="true" />New clips above</button>}
    <div ref={feed} className={styles.feed} role="region" aria-label="Cosmos review history" tabIndex={0} onScroll={recordPosition}
      onPlayCapture={event => {
        const playing = event.target as HTMLVideoElement;
        const id = playing.closest<HTMLElement>('[data-event-id]')?.dataset.eventId;
        const playingEvent = sorted.find(item => item.id === id); if (playingEvent) setPlaybackEvent(playingEvent);
        feed.current?.querySelectorAll('video').forEach(video => { if (video !== playing && !video.paused) video.pause(); });
      }}>
      {!available && <p className={styles.availability}>Review history is unavailable. Any saved evidence shown below remains tied to its original clip.</p>}
      {sorted.length ? sorted.map((event, index) => <ReviewRow key={event.id} event={event} root={feed} first={index === 0}
        incident={incidents.find(incident => incident.candidate_ids?.includes(event.id) || incident.representative_event_id === event.id)}
        streamRevision={streamRevision} activeRule={activeRule} ask={addToChat} attached={attachedId === event.id} />)
        : <div className={styles.empty}><IconClock size={25} aria-hidden="true" /><h3>{!available ? 'Waiting for review history' : autoReview ? 'Waiting for a D1 candidate' : 'Automatic verification is off'}</h3><p>{autoReview ? 'When D1’s trigger rule matches, the event clip and Cosmos’s verification appear here.' : 'Open Monitoring setup to review the trigger configuration.'}</p><span>The monitored video stays visible above.</span></div>}
    </div>
    </div>
    <aside ref={chat} className={styles.chatDock} aria-label="Chat with Cosmos">
      <header className={styles.chatHeading}><IconMessageCircle size={19} aria-hidden="true" /><div><h3>Chat with Cosmos</h3></div></header>
      {qaEvent ? <div className={styles.attachment} data-testid="chat-attachment" data-attached-event-id={qaEvent.id}>
        <IconMovie size={24} aria-hidden="true" /><div><strong>Event clip attached</strong><time dateTime={new Date(qaEvent.captured_at * 1000).toISOString()}>{savedTime(qaEvent.captured_at)}</time></div>
        <button type="button" aria-label="Remove clip from chat" onClick={() => setAttachedId(null)}><IconX size={16} aria-hidden="true" /></button>
      </div> : <div className={styles.chatEmpty}>
        <IconPaperclip size={28} aria-hidden="true" /><h4>Add a clip to start</h4><p>Choose <strong>Add to chat</strong> on an event, then ask Cosmos about that video.</p>
        <div className={styles.emptyComposer}><textarea aria-label="Question about attached clip" disabled rows={2} placeholder="Add an event clip to ask about it…" /><button disabled aria-label="Send question"><IconArrowUp size={18} aria-hidden="true" /></button></div>
      </div>}
      <div className={styles.chatThreads} hidden={!qaEvent}>
        {chatEvents.map(event => <div key={event.id} className={styles.chatThread} hidden={event.id !== attachedId}>
          <EventQuestions eventId={event.id} clipReady={Boolean(event.clip_ready)} clipTime={savedTime(event.captured_at)}
            active={event.id === attachedId} focusRequest={event.id === attachedId ? focusRequest : 0}
            onPendingChange={setChatPending} busyElsewhere={pendingIds.size > 0 && !pendingIds.has(event.id)} />
        </div>)}
      </div>
      {pendingIds.size > 0 && !qaEvent && <p className={styles.detachedPending} role="status">A saved clip has a pending question. Add it again to return to its conversation.</p>}
    </aside>
  </div>;
}

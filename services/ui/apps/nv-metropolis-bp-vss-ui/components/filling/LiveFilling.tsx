// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT
import { useCallback, useEffect, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { api, percent } from './api';
import { Activity, ArrowUpRight, Database, LoaderCircle, MessageSquare, Play, RefreshCw, ScanLine, ShieldCheck } from './icons';
import { bottleSelection, toggleBottleSelection, type LiveBottleSelection, currentPresentation, liveBottleLabel, liveBottleQuestion, liveChatContext, type LiveChatContext, decodeLiveFrame, isLiveFrame, PairedFrameQueue, paintLiveFrame, safeEvidenceUrl, type DecodedFrame, type LiveEvent, type LiveFrame, type LiveSession, type LiveSource } from './liveFrames';

const terminal = new Set(['idle', 'stopped', 'error', 'unavailable', 'complete']);
const human = (value?: string) => value ? value.replaceAll('_', ' ').replaceAll('-', ' ') : 'Waiting for observations';
const numeric = (value: unknown, digits = 1) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
const timestamp = (value: string | number) => typeof value === 'number' ? value > 1e12 ? value : value * 1000 : Date.parse(value);

export default function LiveFilling({theme = 'dark', active = true, submitChatMessage, onChatContextChange}: {theme?: 'light' | 'dark'; active?: boolean; submitChatMessage?: (message: string) => void; onChatContextChange?: (context: LiveChatContext | null) => void}) {
  const [sources, setSources] = useState<LiveSource[]>([]), [choice, setChoice] = useState('');
  const [session, setSession] = useState<LiveSession>({status: 'idle'}), [frame, setFrame] = useState<LiveFrame | null>(null);
  const [events, setEvents] = useState<LiveEvent[]>([]), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [connection, setConnection] = useState('disconnected'), [retry, setRetry] = useState(0), [now, setNow] = useState(Date.now());
  const [showBottle, setShowBottle] = useState(true), [showLiquid, setShowLiquid] = useState(true);
  const canvas = useRef<HTMLCanvasElement>(null), pair = useRef<{frame: LiveFrame; decoded: DecodedFrame} | null>(null);
  const toggles = useRef({showBottle, showLiquid}), receivedAt = useRef(0), actionPending = useRef(false), actionGeneration = useRef(0), mounted = useRef(true);
  const [selectedBottles, setSelectedBottles] = useState<LiveBottleSelection[]>([]);
  useEffect(() => setSelectedBottles([]), [session.session_id, session.stream_id]);
  const [viewerDrops, setViewerDrops] = useState(0);
  const eventCursor = useRef<{sessionId?: string; seq: number; loaded?: boolean}>({seq: 0});
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const loadSources = useCallback(async () => {
    try { const result = await api<{sources: LiveSource[]}>('/filling/api/live/sources'); if (mounted.current) { setSources(result.sources || []); setError(''); } }
    catch (failure) { if (mounted.current) setError((failure as Error).message); }
  }, []);
  useEffect(() => { void loadSources(); }, [loadSources]);
  useEffect(() => {
    if (!active) return;
    let alive = true, timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const generation = actionGeneration.current;
      try {
        const next = await api<LiveSession>('/filling/api/live/status');
        if (!alive || actionPending.current || generation !== actionGeneration.current) return;
        setSession(next);
        if (next.session_id && next.stream_id) {
          setChoice(next.stream_id);
          if (eventCursor.current.sessionId !== next.session_id) { eventCursor.current = {sessionId: next.session_id, seq: 0}; setEvents([]); }
          const result = await api<{session_id: string; stream_id: string; events: LiveEvent[]; next_seq: number}>(`/filling/api/live/events?session_id=${encodeURIComponent(next.session_id)}&after_seq=${eventCursor.current.seq}&limit=30${eventCursor.current.loaded ? '' : '&latest=true'}`);
          if (alive && generation === actionGeneration.current && result.session_id === next.session_id && result.stream_id === next.stream_id) {
            const incoming = (result.events || []).filter(event => event.session_id === next.session_id && event.stream_id === next.stream_id);
            setEvents(previous => Array.from(new Map([...previous.filter(event => event.session_id === next.session_id), ...incoming].map(event => [event.event_id, event])).values()).sort((a, b) => b.seq - a.seq).slice(0, 30));
            eventCursor.current.loaded = true;
            if (Number.isSafeInteger(result.next_seq)) eventCursor.current.seq = Math.max(eventCursor.current.seq, result.next_seq);
          }
        }
      } catch (failure) { if (alive) setError((failure as Error).message); }
      finally { if (alive) timer = setTimeout(poll, 2000); }
    };
    void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [active, retry]);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 500); return () => clearInterval(timer); }, []);
  const running = !!session.session_id && !!session.stream_id && !terminal.has(session.status);
  useEffect(() => {
    pair.current?.decoded.close(); pair.current = null; setFrame(null); receivedAt.current = 0; setViewerDrops(0);
    canvas.current?.getContext('2d')?.clearRect(0, 0, canvas.current.width, canvas.current.height);
    if (!active || !running || !session.session_id || !session.stream_id) { setConnection('disconnected'); return; }
    let disposed = false;
    const selected = {session_id: session.session_id, stream_id: session.stream_id};
    setConnection('connecting');
    const queue = new PairedFrameQueue(decodeLiveFrame, (next, decoded) => {
      if (disposed || !canvas.current) { decoded.close(); return; }
      paintLiveFrame(canvas.current, next, decoded, toggles.current.showBottle, toggles.current.showLiquid);
      pair.current?.decoded.close(); pair.current = {frame: next, decoded};
      receivedAt.current = Date.now();
      flushSync(() => { setFrame(next); setConnection('connected'); setViewerDrops(queue.dropped); setError(''); });
    }, failure => setError(failure.message));
    const url = new URL('/filling/api/live/ws', window.location.origin);
    url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    url.searchParams.set('session_id', selected.session_id);
    const socket = new WebSocket(url);
    socket.onopen = () => { if (!disposed) setConnection('waiting'); };
    socket.onmessage = event => {
      if (disposed) return;
      try {
        const value: unknown = JSON.parse(event.data);
        if (isLiveFrame(value, selected)) queue.push(value);
        else if (value && typeof value === 'object' && (value as {type?: string}).type === 'error') {
          const failure = value as {session_id?: string; error?: string};
          if (failure.session_id === selected.session_id) setError(failure.error || 'Live processing reported an error.');
        }
      } catch { setError('The live preview received an invalid frame packet.'); }
    };
    socket.onerror = () => { if (!disposed) setConnection('disconnected'); };
    socket.onclose = () => { if (!disposed) setConnection('disconnected'); };
    return () => { disposed = true; queue.dispose(); socket.close(); pair.current?.decoded.close(); pair.current = null; };
  }, [active, running, session.session_id, session.stream_id, retry]);
  useEffect(() => {
    toggles.current = {showBottle, showLiquid};
    if (canvas.current && pair.current) paintLiveFrame(canvas.current, pair.current.frame, pair.current.decoded, showBottle, showLiquid);
  }, [showBottle, showLiquid]);
  const start = async () => {
    if (!choice || busy || !sources.some(source => source.stream_id === choice && source.approved === true)) return;
    actionPending.current = true; actionGeneration.current += 1; setBusy(true); setError('');
    try { const next = await api<LiveSession>('/filling/api/live/start', {method: 'POST', body: JSON.stringify({stream_id: choice})}); if (next.stream_id !== choice || !next.session_id) throw new Error('The live session does not match the selected VSS stream.'); setSession(next); setEvents([]); setRetry(value => value + 1); }
    catch (failure) { setError((failure as Error).message); }
    finally { actionPending.current = false; setBusy(false); }
  };
  const stop = async () => {
    if (!session.session_id || busy) return;
    actionPending.current = true; actionGeneration.current += 1; setBusy(true); setError('');
    try { const next = await api<LiveSession>('/filling/api/live/stop', {method: 'POST', body: JSON.stringify({session_id: session.session_id})}); if (next.session_id !== session.session_id) throw new Error('Stop response belongs to a different live session.'); setSession(next); }
    catch (failure) { setError((failure as Error).message); }
    finally { actionPending.current = false; setBusy(false); }
  };
  const source = sources.find(value => value.stream_id === (session.stream_id || choice));
  const transportStale = !!frame && now - receivedAt.current > 3000;
  const sourceAge = frame ? Math.max(0, (now - timestamp(frame.received_at_utc)) / 1000) : null;
  const stale = transportStale || (sourceAge !== null && sourceAge > 5);
  const live = session.status === 'running' && connection === 'connected' && !stale;
  const current = live ? frame?.current : null;
  const presentation = currentPresentation(current);
  const frameEpoch = frame?.session_id === session.session_id && frame?.stream_id === session.stream_id ? frame?.epoch : undefined;
  const scopeEpoch = frameEpoch === undefined ? session.epoch : Math.max(frameEpoch, session.epoch ?? frameEpoch);
  useEffect(() => {
    onChatContextChange?.(active ? liveChatContext(session, scopeEpoch, selectedBottles) : null);
    return () => onChatContextChange?.(null);
  }, [active, session.session_id, session.stream_id, scopeEpoch, selectedBottles, onChatContextChange]);
  const selectBottle = (trackId: string, epoch: number, eventId?: string, frameId?: number) => {
    const target = bottleSelection(session, trackId, epoch, eventId, frameId);
    if (target) setSelectedBottles(previous => toggleBottleSelection(previous, target));
  };
  const askBottle = (trackId: string, epoch: number, eventId?: string) => {
    const question = liveBottleQuestion(session, trackId, epoch, eventId);
    if (question) submitChatMessage?.(question);
  };
  const counts = session.summary || frame?.summary;
  const status = !running ? human(session.status) : session.status === 'reconnecting' ? 'Reconnecting source' : session.status === 'connecting' ? 'Connecting source' : connection === 'disconnected' ? 'Disconnected' : stale ? 'Stale preview' : live ? 'Monitoring' : 'Waiting for processed frames';
  return <div className={`vss-filling ${theme === 'light' ? 'vss-filling-light' : ''}`}><main>
    <section className="native-heading"><div><h2>Live filling monitor</h2><p>Measure incoming VSS stream frames, with bottle and liquid masks kept together.</p></div><span className={`live-state ${live ? 'live-state-ok' : ''}`} role="status"><span className="status-dot"/>{status}</span></section>
    <section className="native-source-selector"><div className="native-source-control"><label htmlFor="vss-filling-live-source">VSS live source</label><select id="vss-filling-live-source" value={choice} disabled={running || busy} onChange={event => setChoice(event.target.value)}><option value="">Select a registered live stream…</option>{sources.map(item => <option key={item.stream_id} value={item.stream_id} disabled={item.approved !== true}>{item.name}{item.approved !== true ? ' · unsupported camera profile' : ` · ${item.state}`}</option>)}</select><button className="button primary" onClick={() => void start()} disabled={running || busy || !sources.some(source => source.stream_id === choice && source.approved === true)}>{busy ? <LoaderCircle className="spin" size={15}/> : <Play size={15}/>}Start monitoring</button><button className="button secondary" disabled={!running || busy} onClick={() => void stop()}>Stop</button><button className="icon-button" aria-label="Refresh live sources" disabled={busy} onClick={() => void loadSources()}><RefreshCw size={15}/></button></div><p>Analysis runs as frames arrive. Leaving this tab pauses the preview; use Stop to end monitoring.</p>{session.stream_id && <div className="native-source-identity"><ShieldCheck size={13}/><span>VSS stream {session.stream_id}</span><span>·</span><span>Session {session.session_id?.slice(0, 12)}</span><span>· Native chat uses this live session</span></div>}</section>
    {(error || session.error) && <div className="error-banner" role="alert">{error || session.error}</div>}
    <div className="workspace-grid live-workspace">
      <section className="panel video-panel"><div className="panel-topline"><div className="panel-title"><Activity size={16}/><h2>{source?.name || 'Live stream preview'}</h2><span className="pill subtle">INCOMING STREAM</span></div>{running && connection === 'disconnected' && <button className="text-button" onClick={() => setRetry(value => value + 1)}>Reconnect preview <RefreshCw size={14}/></button>}</div>
        <div className={`live-video-stage ${!live && frame ? 'live-video-stale' : ''}`} style={{aspectRatio: `${frame?.width || source?.width || 1280}/${frame?.height || source?.height || 720}`}}>
          <canvas ref={canvas} aria-label="Live frame with matching RF-DETR masks"/>
          {!frame && <div className="live-preview-empty"><ScanLine size={30}/><strong>{running ? 'Waiting for live inference' : 'Select a stream and start monitoring'}</strong><span>{running ? 'The preview appears only when a real frame and its measurements arrive together.' : 'No saved recording is substituted.'}</span></div>}
          {!!frame && !live && <div className="live-preview-warning"><strong>{status}</strong><span>Last received frame · current height unavailable</span></div>}
          {frame && <div className="live-frame-stamp">Frame {frame.frame_id} · PTS {frame.source_pts_seconds.toFixed(3)}s</div>}
        </div>
        <div className="rf-segmentation-options live-preview-controls"><label className="rf-bottle-toggle"><input type="checkbox" checked={showBottle} onChange={event => setShowBottle(event.target.checked)}/><i/>Bottle outline</label><label className="rf-liquid-toggle"><input type="checkbox" checked={showLiquid} onChange={event => setShowLiquid(event.target.checked)}/><i/>Liquid mask</label><span className="small-note">Paired frame + measurements</span></div>
      </section>
      <section className="panel live-current"><div className="panel-topline"><div className="panel-title"><ScanLine size={16}/><h2>Current bottle</h2></div><span className="pill subtle">PROVISIONAL</span></div><div className="live-current-body"><span className="eyebrow">VISIBLE LIQUID HEIGHT</span><strong className="live-height" data-frame-id={frame?.frame_id}>{percent(presentation.level)}</strong><h3>{presentation.title}</h3><p>{presentation.description}</p><button className="button secondary live-ask" disabled={!submitChatMessage || !current?.track_id || !frame || !live} onClick={() => {if (current?.track_id && frame) askBottle(current.track_id, frame.epoch);}}><MessageSquare size={15}/>Ask about this bottle</button><button className="button secondary" disabled={!current?.track_id || !frame || !live || (selectedBottles.length >= 2 && !selectedBottles.some(value => liveBottleLabel(value.track_id) === current?.track_id))} onClick={() => {if (current?.track_id && frame) selectBottle(current.track_id, frame.epoch, undefined, frame.frame_id);}}>Select this bottle for chat</button><dl className="live-facts"><div><dt>Bottle</dt><dd title={current?.track_id || undefined}>{current?.track_id ? liveBottleLabel(current.track_id) : '—'}</dd></div><div><dt>Camera reference</dt><dd>{percent(current?.reference_level ?? session.measurement?.reference_level ?? null)}</dd></div><div><dt>Frame age since receipt</dt><dd>{frame ? `${numeric(sourceAge)}s` : '—'}</dd></div><div><dt>Inference rate</dt><dd>{numeric(frame?.stats.inference_fps)} fps</dd></div><div><dt>Receiver queue age</dt><dd>{numeric(frame?.stats.queue_age_ms, 0)} ms</dd></div><div><dt>Viewer frames skipped</dt><dd>{viewerDrops}</dd></div></dl><div className="measurement-note"><ShieldCheck size={15}/><p>Height comes from RF-DETR masks. Overflow uses a separate exterior-liquid check. A bottle is counted after its observed cycle finishes. Frame age starts at receiver time, not verified camera capture time.</p></div></div></section>
    </div>
    <section className="live-results"><div className="live-section-heading"><h3>Completed this session</h3><span className="small-note">Observed results, not the expected totals in the source video</span></div><div className="cycle-summary">{(['total', 'normal', 'underfill', 'overflow', 'uncertain'] as const).map(key => <div key={key} className={`cycle-${key}`}><span>{key === 'total' ? 'Completed bottles' : human(key)}</span><strong>{counts?.[key] ?? '—'}</strong></div>)}</div></section>
    <section className="panel" aria-label="Bottles selected for chat"><div className="panel-topline"><h3>Selected for chat</h3><button className="text-button" disabled={!selectedBottles.length} onClick={() => setSelectedBottles([])}>Clear selection</button></div><p>{selectedBottles.length ? selectedBottles.map(value => `${liveBottleLabel(value.track_id)} (epoch ${value.epoch})`).join(' + ') : 'Select one bottle to ask about it, or two to compare. The selection stays fixed as the stream moves.'}</p>{selectedBottles.map(value => <button className="button secondary" key={value.track_id} onClick={() => setSelectedBottles(previous => previous.filter(item => item.track_id !== value.track_id))}>Remove {liveBottleLabel(value.track_id)}</button>)}</section>
    <section className="panel live-events"><div className="panel-topline"><div className="panel-title"><Database size={16}/><h2>Recent observations</h2></div><span className="small-note">Latest 30 observations</span></div>{events.length ? <div className="cycle-table-scroll"><table className="cycle-table"><thead><tr><th>Select</th><th>Bottle</th><th>Result</th><th>Final height</th><th>Evidence</th><th>Ask VSS Agent</th></tr></thead><tbody>{events.map(event => { const link = safeEvidenceUrl(event); return <tr key={event.event_id}><td><input type="checkbox" aria-label={`Select ${liveBottleLabel(event.track_id)} for chat`} checked={selectedBottles.some(value => value.track_id === event.track_id)} disabled={!event.track_id || (selectedBottles.length >= 2 && !selectedBottles.some(value => value.track_id === event.track_id))} onChange={() => {if (event.track_id) selectBottle(event.track_id, event.epoch, event.event_id);}}/></td><th title={event.track_id || event.event_id}>{liveBottleLabel(event.track_id)}</th><td title={event.reason}>{human(event.status)}{event.kind === 'overflow.detected' && <span className="small-note"> · observed, cycle in progress</span>}</td><td>{percent(event.final_level ?? null)}</td><td>{link ? <a className="text-button" href={link} target="_blank" rel="noreferrer">Open VIOS evidence <ArrowUpRight size={13}/></a> : <span className="small-note">{event.evidence_status === 'unavailable' ? 'Evidence unavailable' : 'Evidence pending'}</span>}</td><td><button className="text-button" disabled={!submitChatMessage || !event.track_id} onClick={() => {if (event.track_id) askBottle(event.track_id, event.epoch, event.event_id);}} aria-label={`Ask about ${liveBottleLabel(event.track_id)}`}><MessageSquare size={14}/>Ask</button></td></tr>; })}</tbody></table></div> : <div className="live-events-empty">No cycle or overflow observations have been received for this session.</div>}</section>
    {session.measurement && <details className="rf-model-provenance live-provenance"><summary>Live model provenance</summary><pre>{JSON.stringify(session.measurement, null, 2)}</pre><p>Visible height is not volume. The liquid model is adapted to the reviewed filling camera.</p></details>}
  </main></div>;
}

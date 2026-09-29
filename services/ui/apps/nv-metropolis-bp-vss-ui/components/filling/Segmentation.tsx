// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { flushSync } from 'react-dom';
import { api } from './api';
import { Layers3, LoaderCircle, RefreshCw, ScanLine } from './icons';
import type { Source } from './types';

type Polygon = [number, number][];
export type SegmentationInstance = {
  id: string; box: [number, number, number, number];
  bottle_mask: Polygon[]; liquid_mask: Polygon[];
  bottle_confidence: number; liquid_confidence: number | null;
};
export type SegmentationSample = { t: number; frame_index?: number; instances: SegmentationInstance[] };
type Model = { name: string; checkpoint_sha256: string; training?: string | Record<string, unknown> };
type Models = { bottle: Model; liquid: Model };
type SegmentationJob = {
  status: 'idle' | 'queued' | 'running' | 'complete' | 'error' | 'unavailable'; progress: number;
  source_id?: string; source_sha256?: string; cached?: boolean; error?: string; models?: Models;
};
export type SegmentationResult = {
  schema_version: number; source_id: string; source_sha256: string; sample_fps: number; source_fps?: number;
  models: Models; device: string; runtime_seconds: number; samples: SegmentationSample[];
  provenance: { mode: string; liquid_training?: string; [key: string]: unknown };
};
const idle: SegmentationJob = { status: 'idle', progress: 0 };
const sameSource = (value: {source_id?: string; source_sha256?: string}, source: Pick<Source, 'id' | 'sha256'>) =>
  value.source_id === source.id && value.source_sha256 === source.sha256;

export function exactSegmentationSample(result: SegmentationResult | null, source: Source | null, time: number): SegmentationSample | undefined {
  if (!result || !source || !sameSource(result, source) || !result.samples.length || !Number.isFinite(time)
      || result.schema_version < 2 || !Number.isFinite(result.source_fps) || !result.source_fps || result.source_fps <= 0
      || Math.abs(result.sample_fps - result.source_fps) > .001 || Math.abs(source.fps - result.source_fps) > .001) return undefined;
  const frameIndex = Math.round(time * result.source_fps);
  let low = 0, high = result.samples.length - 1;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if ((result.samples[middle].frame_index ?? -1) < frameIndex) low = middle + 1; else high = middle;
  }
  const found = result.samples[low];
  // Only the decoded frame's own inference is eligible. Missing frames stay absent.
  return found.frame_index === frameIndex && Math.abs(found.t - time) <= .5 / result.source_fps ? found : undefined;
}

/** The main overlay follows decoded video frames, not the browser's timeupdate timer. */
export function FrameSynchronizedSegmentation({video, result, source, showBottle, showLiquid, active = true}: {
  video: RefObject<HTMLVideoElement | null>; result: SegmentationResult | null; source: Source;
  showBottle: boolean; showLiquid: boolean; active?: boolean;
}) {
  const [presentedTime, setPresentedTime] = useState<number | null>(null);
  useEffect(() => {
    const element = video.current;
    setPresentedTime(null);
    if (!element || !active || !('requestVideoFrameCallback' in element)) return;
    let cancelled = false, handle = 0, decodedTime: number | null = null;
    const invalidate = () => { flushSync(() => setPresentedTime(null)); };
    const empty = () => { decodedTime = null; invalidate(); };
    const showDecodedSeekFrame = () => {
      // Chrome can deliver the decoded frame callback before clearing `seeking`.
      // Reuse only that actual callback's timestamp, and only for this seek frame.
      const matchesPosition = decodedTime !== null && Number.isFinite(source.fps) && source.fps > 0
        && element.currentTime >= decodedTime - 1e-5
        && element.currentTime < decodedTime + 1 / source.fps;
      if (!element.seeking && element.readyState >= 2 && matchesPosition) {
        flushSync(() => setPresentedTime(decodedTime));
      }
    };
    const present: VideoFrameRequestCallback = (_now, metadata) => {
      if (cancelled) return;
      decodedTime = metadata.mediaTime;
      // Commit before this decoded frame is presented; no old mask survives a seek.
      flushSync(() => setPresentedTime(element.seeking ? null : metadata.mediaTime));
      handle = element.requestVideoFrameCallback(present);
    };
    element.addEventListener('seeking', invalidate);
    element.addEventListener('seeked', showDecodedSeekFrame);
    element.addEventListener('emptied', empty);
    handle = element.requestVideoFrameCallback(present);
    return () => {
      cancelled = true; element.cancelVideoFrameCallback(handle);
      element.removeEventListener('seeking', invalidate); element.removeEventListener('emptied', empty);
      element.removeEventListener('seeked', showDecodedSeekFrame);
    };
  }, [video, source.id, source.sha256, source.fps, active]);
  const sample = presentedTime === null ? undefined : exactSegmentationSample(result, source, presentedTime);
  return <SegmentationOverlay sample={sample} source={source} showBottle={showBottle} showLiquid={showLiquid} mediaTime={presentedTime}/>;
}

export function useSegmentation(source: Source | null, time: number, active = true, refreshKey?: string) {
  const [job, setJob] = useState<SegmentationJob>(idle);
  const [result, setResult] = useState<SegmentationResult | null>(null);
  const [error, setError] = useState('');
  const [showBottle, setShowBottle] = useState(true), [showLiquid, setShowLiquid] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [reload, setReload] = useState(0);
  const observedCache = useRef<{sourceId?: string; key?: string}>({});
  const generation = useRef(0), resultRef = useRef<SegmentationResult | null>(null);
  useEffect(() => {
    const previous = observedCache.current;
    if (previous.sourceId !== source?.id) {
      observedCache.current = {sourceId: source?.id, key: refreshKey};
      return;
    }
    // Initial analysis metadata identifies the result already being downloaded.
    // Only replacement of a known cache key should abort and refetch that payload.
    if (refreshKey) {
      if (previous.key && previous.key !== refreshKey) setReload(value => value + 1);
      observedCache.current = {sourceId: source?.id, key: refreshKey};
    }
  }, [source?.id, refreshKey]);
  useEffect(() => {
    const token = ++generation.current;
    setJob(idle); setResult(null); resultRef.current = null; setError(''); setSubmitting(false);
    if (!source || !active) return;
    const selected = {id: source.id, sha256: source.sha256};
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const status = await api<SegmentationJob>(`/filling/api/segmentation?source_id=${encodeURIComponent(selected.id)}`, {signal: controller.signal});
        if (generation.current !== token) return;
        if (!sameSource(status, selected)) throw new Error('Segmentation status belongs to a different recording. Reload the selected source.');
        setJob(status); setError('');
        if (status.status === 'complete' && !resultRef.current) {
          const next = await api<SegmentationResult>(`/filling/api/segmentation/result?source_id=${encodeURIComponent(selected.id)}`, {signal: controller.signal});
          if (generation.current !== token) return;
          if (!sameSource(next, selected) || !Array.isArray(next.samples) || !Number.isFinite(next.sample_fps) || next.sample_fps <= 0) {
            throw new Error('Segmentation result does not match this recording or its sample cadence.');
          }
          resultRef.current = next; setResult(next);
        } else if (status.status !== 'complete') {
          resultRef.current = null; setResult(null);
        }
      } catch (failure) {
        if (generation.current === token && !controller.signal.aborted) {
          setError((failure as Error).message); resultRef.current = null; setResult(null);
        }
      } finally {
        if (generation.current === token && !controller.signal.aborted) timer = setTimeout(poll, 2500);
      }
    };
    void poll();
    return () => { generation.current += 1; controller.abort(); if (timer) clearTimeout(timer); };
  }, [source?.id, source?.sha256, active, reload]);
  const run = useCallback(async () => {
    if (!source || submitting || job.status === 'queued' || job.status === 'running') return;
    const token = generation.current;
    setSubmitting(true); setError('');
    try {
      const next = await api<SegmentationJob>('/filling/api/segmentation', {method: 'POST', body: JSON.stringify({source_id: source.id, force: !!resultRef.current})});
      if (generation.current !== token) return;
      if (!sameSource(next, source)) throw new Error('Segmentation job belongs to a different recording.');
      resultRef.current = null; setResult(null); setJob(next);
    } catch (failure) {
      if (generation.current === token) setError((failure as Error).message);
    } finally {
      if (generation.current === token) setSubmitting(false);
    }
  }, [source?.id, source?.sha256, submitting, job.status]);
  const sample = useMemo(() => exactSegmentationSample(result, source, time), [result, source, time]);
  return {job, result, error, sample, showBottle, setShowBottle, showLiquid, setShowLiquid, submitting, run};
}
export type SegmentationState = ReturnType<typeof useSegmentation>;

const validPolygon = (polygon: Polygon) => Array.isArray(polygon) && polygon.length >= 3
  && polygon.every(point => Array.isArray(point) && point.length === 2 && point.every(value => Number.isFinite(value) && value >= 0 && value <= 1));

export function SegmentationOverlay({sample, source, showBottle, showLiquid, mediaTime}: {
  sample?: SegmentationSample; source: Source; showBottle: boolean; showLiquid: boolean; mediaTime?: number | null;
}) {
  if (!sample || (!showBottle && !showLiquid)) return null;
  const points = (polygon: Polygon) => polygon.map(([x, y]) => `${x * source.width},${y * source.height}`).join(' ');
  return <svg className="rf-segmentation-overlay" viewBox={`0 0 ${source.width} ${source.height}`} aria-label="RF-DETR bottle and liquid model masks" data-sample-time={sample.t} data-frame-index={sample.frame_index} data-media-time={mediaTime}>
    {sample.instances.map(instance => <g key={instance.id} data-instance-id={instance.id}>
      {showLiquid && (instance.liquid_mask || []).filter(validPolygon).map((polygon, index) =>
        <polygon key={`liquid-${index}`} points={points(polygon)} fill="#ff9e36" fillOpacity=".42" stroke="#ffae50" strokeWidth={source.width * .0015} />)}
      {showBottle && (instance.bottle_mask || []).filter(validPolygon).map((polygon, index) =>
        <polygon key={`bottle-${index}`} points={points(polygon)} fill="#42aaff" fillOpacity=".05" stroke="#54b9ff" strokeWidth={source.width * .0022} strokeLinejoin="round" />)}
    </g>)}
  </svg>;
}

const trainingLabel = (value: Model['training']) => typeof value === 'string' ? value : value ? Object.entries(value).map(([key, item]) => `${key}: ${typeof item === 'string' || typeof item === 'number' ? item : 'recorded in provenance'}`).join(' · ') : 'Not reported';
export function SegmentationControls({state, source, unified = false, neuralMeasurements = false}: {state: SegmentationState; source: Source | null; unified?: boolean; neuralMeasurements?: boolean}) {
  const busy = state.submitting || state.job.status === 'running' || state.job.status === 'queued';
  const models = state.result?.models || state.job.models;
  const progress = Math.max(0, Math.min(100, state.job.progress * 100));
  return <section className="rf-segmentation-controls" aria-label="RF-DETR segmentation controls">
    <div className="rf-segmentation-heading"><div><h3><ScanLine size={15}/>RF-DETR segmentation</h3><p>Learned bottle outlines and liquid masks on this recording.</p></div>
      {!unified && <button className="button secondary" disabled={!source || busy} onClick={() => void state.run()}>{busy ? <LoaderCircle className="spin" size={14}/> : state.result ? <RefreshCw size={14}/> : <Layers3 size={14}/>} {busy ? 'Segmenting…' : state.result ? 'Re-run segmentation' : 'Run segmentation'}</button>}
    </div>
    <div className="rf-segmentation-options"><label className="rf-bottle-toggle"><input type="checkbox" checked={state.showBottle} onChange={event => state.setShowBottle(event.target.checked)}/><i/>Bottle outline</label><label className="rf-liquid-toggle"><input type="checkbox" checked={state.showLiquid} onChange={event => state.setShowLiquid(event.target.checked)}/><i/>Liquid mask</label>
      <span className="rf-sample-state" role="status">{state.result ? state.result.schema_version >= 2 ? `${state.result.source_fps} fps · source-frame masks ready` : 'Sparse masks require recomputation' : state.job.status === 'unavailable' ? 'Models unavailable' : busy ? state.job.status === 'queued' ? 'Queued' : `${Math.round(progress)}% processed` : 'No segmentation result'}</span>
    </div>
    {busy && <div className="rf-segmentation-progress" role="progressbar" aria-label="Segmentation progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress)}><i style={{width: `${Math.max(1, progress)}%`}}/></div>}
    {(state.error || state.job.error) && <p className="error-text" role="alert">{state.error || state.job.error}</p>}
    <p className="rf-measurement-separation">{neuralMeasurements ? 'Fill-height curves and underfill assessments come from these RF-DETR masks. Overflow uses a separate exterior-liquid check.' : unified ? 'Analyze recording computes model masks and derives fill-height measurements together.' : 'Legacy calibrated measurements remain separate from RF-DETR masks for this recording.'}{state.result && state.result.schema_version >= 2 ? ` Analyzed replay at ${state.result.source_fps} fps, synchronized to decoded source frames.` : state.result ? ' This older sparse result is hidden; re-run analysis for frame-synchronized masks.' : ''}</p>
    {models && <details className="rf-model-provenance"><summary>Model provenance{state.result ? ` · ${state.result.runtime_seconds.toFixed(1)}s compute` : ''}</summary><dl>{(['bottle', 'liquid'] as const).map(role => {
      const model = models[role];
      return model ? <div key={role}><dt>{role === 'bottle' ? 'Bottle model' : 'Liquid model'}</dt><dd>{model.name}<small>{trainingLabel(model.training)}</small><code title={model.checkpoint_sha256}>Checkpoint {model.checkpoint_sha256?.slice(0, 16) || 'unreported'}…</code></dd></div> : null;
    })}</dl><p>Model scores are not calibrated probabilities. {state.result?.provenance.liquid_training || ''}</p></details>}
  </section>;
}

// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT
import type { SegmentationInstance } from './Segmentation';

export type LiveSource = {stream_id: string; name: string; state: string; width?: number; height?: number; fps?: number; profile_id?: string; approved?: boolean; reason?: string};
export type LiveBottleSelection = {track_id: string; epoch: number; event_id?: string; frame_id?: number};
export type LiveChatContext = {id: string; label: string; contextType: string; data: Record<string, unknown>};
export type LiveCurrent = {quality?: {readable: boolean; reason?: string}; overflow?: {observed: boolean; engine: string; first_observed_pts_seconds?: number; provisional: boolean}; track_id?: string | null; level?: number | null; phase?: string; reason?: string; reference_level?: number; provisional?: boolean};
export type LiveCounts = {total: number; normal: number; underfill: number; overflow: number; uncertain: number; incomplete?: number};
export type LiveStats = {processed_frames?: number; inference_fps?: number; capture_fps?: number; dropped_frames?: number; completed?: LiveCounts; counts?: LiveCounts; [key: string]: unknown};
export type LiveFrame = {type: 'frame'; session_id: string; stream_id: string; frame_id: number; source_pts_seconds: number; epoch: number; received_at_utc: string; inference_done_at_utc?: string; width: number; height: number; preview_width: number; preview_height: number; jpeg_base64: string; instances: SegmentationInstance[]; current: LiveCurrent | null; stats: LiveStats; summary: LiveCounts; measurement?: Record<string, unknown>};
export type LiveSession = {epoch?: number; session_id?: string; stream_id?: string; status: string; error?: string; current?: LiveCurrent | null; stats?: LiveStats; summary?: LiveCounts; measurement?: {reference_level?: number; [key: string]: unknown}; source?: {name: string; width: number; height: number; fps: number}; started_at?: string};
export type LiveEvent = {event_id: string; session_id: string; stream_id: string; epoch: number; seq: number; track_id?: string; kind: string; status?: string; final_level?: number | null; source_pts_seconds?: number; reason?: string; evidence_status?: string; video_evidences: {available?: boolean; public_url?: string; local_clip_url?: string}[]};
export type DecodedFrame = {image: CanvasImageSource; width: number; height: number; close: () => void};

export function isLiveFrame(value: unknown, session: Pick<LiveSession, 'session_id' | 'stream_id'>): value is LiveFrame {
  if (!value || typeof value !== 'object') return false;
  const frame = value as LiveFrame;
  return frame.type === 'frame' && !!session.session_id && !!session.stream_id
    && frame.session_id === session.session_id && frame.stream_id === session.stream_id
    && Number.isSafeInteger(frame.frame_id) && frame.frame_id >= 0 && Number.isFinite(frame.source_pts_seconds)
    && Number.isSafeInteger(frame.epoch) && frame.epoch >= 0 && Number.isFinite(Date.parse(frame.received_at_utc))
    && Number.isInteger(frame.preview_width) && frame.preview_width > 0 && frame.preview_width <= 8192
    && Number.isInteger(frame.preview_height) && frame.preview_height > 0 && frame.preview_height <= 8192
    && Number.isInteger(frame.width) && frame.width > 0 && frame.width <= 8192
    && Number.isInteger(frame.height) && frame.height > 0 && frame.height <= 8192
    && typeof frame.jpeg_base64 === 'string' && frame.jpeg_base64.length > 0 && frame.jpeg_base64.length < 12_000_000
    && Array.isArray(frame.instances);
}

/** Only viewer frames are coalesced. Every image stays attached to its own inference packet. */
export class PairedFrameQueue {
  private pending: LiveFrame | null = null;
  private busy = false;
  private disposed = false;
  private latestId = -1;
  private latestEpoch = -1;
  dropped = 0;
  constructor(private decode: (frame: LiveFrame) => Promise<DecodedFrame>, private publish: (frame: LiveFrame, decoded: DecodedFrame) => void, private fail: (error: Error) => void) {}
  push(frame: LiveFrame) {
    if (this.disposed || frame.epoch < this.latestEpoch || (frame.epoch === this.latestEpoch && frame.frame_id <= this.latestId)) return;
    this.latestEpoch = frame.epoch;
    this.latestId = frame.frame_id;
    if (this.pending) this.dropped += 1;
    this.pending = frame;
    void this.consume();
  }
  dispose() { this.disposed = true; this.pending = null; }
  private async consume() {
    if (this.busy || this.disposed) return;
    this.busy = true;
    try {
      while (this.pending && !this.disposed) {
        const frame = this.pending; this.pending = null;
        let decoded: DecodedFrame | undefined;
        try {
          decoded = await this.decode(frame);
          if (this.disposed || frame.epoch < this.latestEpoch) { decoded.close(); continue; }
          if (decoded.width !== frame.preview_width || decoded.height !== frame.preview_height || Math.abs(decoded.width / decoded.height - frame.width / frame.height) > 2 / decoded.height) throw new Error('Preview dimensions do not match the measurement frame.');
          this.publish(frame, decoded);
        } catch (error) {
          decoded?.close();
          if (!this.disposed) this.fail(error instanceof Error ? error : new Error('Live preview frame could not be decoded.'));
        }
      }
    } finally { this.busy = false; }
  }
}

export async function decodeLiveFrame(frame: LiveFrame): Promise<DecodedFrame> {
  const data = Uint8Array.from(atob(frame.jpeg_base64), value => value.charCodeAt(0));
  const image = await createImageBitmap(new Blob([data], {type: 'image/jpeg'}));
  return {image, width: image.width, height: image.height, close: () => image.close()};
}

export function paintLiveFrame(canvas: HTMLCanvasElement, frame: LiveFrame, decoded: DecodedFrame, showBottle: boolean, showLiquid: boolean) {
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Your browser cannot draw the live preview.');
  const width = decoded.width, height = decoded.height;
  canvas.width = width; canvas.height = height;
  context.drawImage(decoded.image, 0, 0, width, height);
  const polygon = (points: [number, number][], kind: 'bottle' | 'liquid') => {
    if (points.length < 3 || !points.every(point => Array.isArray(point) && point.length === 2 && point.every(value => Number.isFinite(value) && value >= 0 && value <= 1))) return;
    context.beginPath();
    points.forEach(([x, y], index) => index ? context.lineTo(x * width, y * height) : context.moveTo(x * width, y * height));
    context.closePath();
    context.strokeStyle = kind === 'bottle' ? '#54b9ff' : '#ffae50';
    context.lineWidth = Math.max(1.5, width * .002);
    if (kind === 'liquid') { context.fillStyle = 'rgba(255,158,54,.42)'; context.fill(); }
    context.stroke();
  };
  for (const instance of frame.instances) {
    if (showLiquid) for (const ring of instance.liquid_mask || []) polygon(ring, 'liquid');
    if (showBottle) for (const ring of instance.bottle_mask || []) polygon(ring, 'bottle');
  }
  canvas.dataset.frameId = String(frame.frame_id); canvas.dataset.sourcePts = String(frame.source_pts_seconds);
  canvas.dataset.epoch = String(frame.epoch); canvas.dataset.sessionId = frame.session_id; canvas.dataset.streamId = frame.stream_id;
}

export function safeEvidenceUrl(event: LiveEvent): string | null {
  const evidence = event.video_evidences?.find(value => value.available !== false && (value.public_url || value.local_clip_url));
  const value = evidence?.public_url || evidence?.local_clip_url;
  if (!value) return null;
  try { const url = new URL(value, window.location.origin); return ['http:', 'https:'].includes(url.protocol) ? value : null; } catch { return null; }
}


export function liveBottleLabel(trackId?: string) {
  return trackId?.match(/(?:^|:)(cycle-\d+)$/)?.[1] || trackId || 'Unknown bottle';
}

/** Actual session scope. view_epoch describes the preview; it must not filter historical cycle questions. */
export function liveChatContext(session: LiveSession, epoch?: number, selected: LiveBottleSelection[] = []): LiveChatContext | null {
  if (!session.session_id || !session.stream_id || !Number.isSafeInteger(epoch) || (epoch as number) < 0) return null;
  return {id: 'filling-live-session', label: 'Live filling session', contextType: 'filling-live', data: {
    mode: 'live', scope: 'selected_live_session', session_id: session.session_id, stream_id: session.stream_id, view_epoch: epoch,
    selected_bottles: selected.filter(value => value.track_id.startsWith(`${session.session_id}:epoch-${value.epoch}:cycle-`)).slice(0, 2),
  }};
}

export function liveBottleQuestion(session: LiveSession, trackId: string, epoch: number, eventId?: string): string | null {
  if (!liveChatContext(session, epoch) || !trackId) return null;
  return `What happened with bottle ${liveBottleLabel(trackId)}? Use the live filling measurements for session ${session.session_id}, stream ${session.stream_id}, epoch ${epoch}, exact track ID ${trackId}${eventId ? `, event ID ${eventId}` : ''}. Query this explicit target even if the live preview has since moved to another bottle or epoch. Separate provisional observations from completed results, and show only available evidence returned by the live query.`;
}

export function currentPresentation(current: LiveCurrent | null | undefined) {
  if (!current) return {level: null, title: 'Waiting for observations', description: 'Fresh measurements appear while the stream is connected.'};
  if (current.overflow?.observed || current.phase === 'overflow_observed') return {level: null, title: 'Overflow observed', description: 'Liquid was observed outside this bottle. Fill height is withheld while the boundary is obscured; the cycle result is still provisional.'};
  if (current.phase === 'departing') return {level: null, title: 'Bottle departing', description: 'The bottle is leaving the measurement area. Its completed result is recorded after the cycle closes.'};
  if (current.quality?.readable === false || current.phase === 'measurement_obscured') return {level: null, title: 'Measurement obscured', description: current.quality?.reason || current.reason || 'The liquid boundary is not reliably visible in this frame.'};
  const level = typeof current.level === 'number' && Number.isFinite(current.level) && current.level >= 0 && current.level <= 1 ? current.level : null;
  return {level, title: current.phase?.replaceAll('_', ' ') || (level === null ? 'Measurement unavailable' : 'Measuring'), description: current.reason || (level === null ? 'No reliable liquid boundary in this frame.' : 'Visible liquid height from this frame’s bottle and liquid masks. The cycle result is still provisional.')};
}

/** Freeze the identity from the actual displayed frame/event, never from later live status. */
export function bottleSelection(session: LiveSession, trackId: string, epoch: number, eventId?: string, frameId?: number): LiveBottleSelection | null {
  if (!session.session_id || !session.stream_id || !Number.isSafeInteger(epoch) || epoch < 1) return null;
  const short = /^cycle-\d+$/.test(trackId);
  const full = short ? `${session.session_id}:epoch-${epoch}:${trackId}` : trackId;
  if (!full.startsWith(`${session.session_id}:epoch-${epoch}:cycle-`) || !/^cycle-\d+$/.test(liveBottleLabel(full))) return null;
  return {track_id: full, epoch, ...(eventId ? {event_id: eventId} : {}), ...(frameId !== undefined ? {frame_id: frameId} : {})};
}
export function toggleBottleSelection(selected: LiveBottleSelection[], target: LiveBottleSelection): LiveBottleSelection[] {
  if (selected.some(value => value.track_id === target.track_id)) return selected.filter(value => value.track_id !== target.track_id);
  return selected.length < 2 ? [...selected, target] : selected;
}

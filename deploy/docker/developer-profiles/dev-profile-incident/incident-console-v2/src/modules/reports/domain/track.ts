// SPDX-License-Identifier: Apache-2.0

// A person's track (the agent's <itemId>-track.json beside the evidence, written when INCIDENT_TRACKING_ENABLED is on):
// [{t, box, score}], t in video seconds, box [ymin, xmin, ymax, xmax] on 0-1000, at most 5 samples a second. Pure
// functions, no I/O, browser-safe: reading the file leniently, the box the report player draws at a second, and the span a
// presence bar shows.

import { boxRegion, containFrame } from '@/lib/evidence/frame-geometry';
import type { ReportView } from '@/modules/reports/domain/report-view';
import type { EvidenceItemType } from '@/modules/reports/domain/revision';

export interface TrackSample {
  t: number;
  box: [number, number, number, number];
}

/** How far outside its first and last sample a track still shows its box, in seconds. */
export const TRACK_TOLERANCE_SECONDS = 0.5;
/** Two samples further apart than this are a gap (the tracker lost the person), not a move to interpolate across. */
export const TRACK_MAX_GAP_SECONDS = 1;

/** The samples of a stored track, sorted by time; a malformed sample is dropped, and anything but an array reads as null. */
export function parseTrackSamples(value: unknown): TrackSample[] | null {
  if (!Array.isArray(value)) return null;
  const samples: TrackSample[] = [];
  for (const raw of value) {
    if (!raw || typeof raw !== 'object') continue;
    const { t, box } = raw as { t?: unknown; box?: unknown };
    if (typeof t !== 'number' || !Number.isFinite(t) || t < 0) continue;
    if (!Array.isArray(box) || !boxRegion(box)) continue;
    samples.push({ t, box: box.slice(0, 4) as TrackSample['box'] });
  }
  return samples.sort((a, b) => a.t - b.t);
}

/**
 * The box to draw at `seconds`: interpolated between the samples either side when they are close, else the nearest
 * sample's box when it is within the tolerance; null outside the track's span (plus the tolerance) and inside a gap.
 */
export function trackBoxAt(
  samples: readonly TrackSample[],
  seconds: number,
  tolerance = TRACK_TOLERANCE_SECONDS,
): TrackSample['box'] | null {
  if (!samples.length || !Number.isFinite(seconds)) return null;
  // The first sample later than `seconds`.
  let lo = 0;
  let hi = samples.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (samples[mid].t <= seconds) lo = mid + 1;
    else hi = mid;
  }
  const before = samples[lo - 1];
  const after = samples[lo];
  if (before && after && after.t - before.t <= TRACK_MAX_GAP_SECONDS) {
    const f = after.t === before.t ? 0 : (seconds - before.t) / (after.t - before.t);
    return before.box.map((value, i) => value + (after.box[i] - value) * f) as TrackSample['box'];
  }
  const nearest = [before, after]
    .filter((sample): sample is TrackSample => !!sample)
    .sort((a, b) => Math.abs(a.t - seconds) - Math.abs(b.t - seconds))[0];
  return Math.abs(nearest.t - seconds) <= tolerance ? nearest.box : null;
}

/**
 * CSS percentages that place a frame box over a player tile showing the whole frame without cutting it (object-contain):
 * the frame fills a centred `containFrame` part of the tile, and the box sits inside that part. Aspects are width / height.
 */
export function containedBoxStyle(
  box: readonly number[] | null | undefined,
  frameAspect: number,
  tileAspect: number,
): { left: string; top: string; width: string; height: string } | null {
  const region = boxRegion(box);
  if (!region) return null;
  const fit = containFrame(frameAspect, tileAspect);
  const pct = (n: number) => `${+(n * 100).toFixed(3)}%`;
  return {
    left: pct((1 - fit.w) / 2 + region.x * fit.w),
    top: pct((1 - fit.h) / 2 + region.y * fit.h),
    width: pct(region.w * fit.w),
    height: pct(region.h * fit.h),
  };
}

/** Smallest bar a presence span is drawn as, as a fraction of the clip, so a short span stays visible and clickable. */
const MIN_PRESENCE_WIDTH = 0.01;

/** Where a span sits on a bar over the whole clip, as fractions; null without a known clip length or a usable span. */
export function presenceRegion(
  firstSeconds: number | null,
  lastSeconds: number | null,
  durationSeconds: number | null,
): { left: number; width: number } | null {
  if (firstSeconds === null || lastSeconds === null || durationSeconds === null) return null;
  if (!(durationSeconds > 0) || !Number.isFinite(firstSeconds) || !Number.isFinite(lastSeconds) || lastSeconds < firstSeconds) return null;
  const clamp = (n: number) => Math.min(1, Math.max(0, n));
  const left = clamp(firstSeconds / durationSeconds);
  if (left >= 1) return null;
  const width = Math.max(MIN_PRESENCE_WIDTH, clamp(lastSeconds / durationSeconds) - left);
  return { left: Math.min(left, 1 - width), width };
}

/**
 * The second a click at `fraction` (0-1) of a presence bar seeks to: the second under the pointer when it is inside the
 * span, else the span's start (the bar is mostly empty clip, where the person is not on screen).
 */
export function presenceSeekSeconds(fraction: number, durationSeconds: number, firstSeconds: number, lastSeconds: number): number {
  const at = fraction * durationSeconds;
  return Number.isFinite(at) && at >= firstSeconds && at <= lastSeconds ? at : firstSeconds;
}

/** One stable colour per tracked item, by its position among the report's tracked items (sorted by id). */
const TRACK_COLOURS = ['#22c55e', '#3b82f6', '#f97316', '#e11d48', '#a855f7', '#14b8a6', '#eab308', '#ec4899'] as const;
export const trackColour = (index: number): string =>
  TRACK_COLOURS[((index % TRACK_COLOURS.length) + TRACK_COLOURS.length) % TRACK_COLOURS.length];

/** An item of the report that has a track, as the overlay and the cards draw it; `label` is its name (the id only when it has none). */
export interface TrackedReportItem {
  kind: EvidenceItemType;
  id: string;
  label: string;
  trackUrl: string;
  firstSeconds: number;
  lastSeconds: number;
  colour: string;
}

/** The report's tracked items (entities, then instruments, then assets, each by id), each with its colour. */
export function trackedReportItems(structured: ReportView['structured']): TrackedReportItem[] {
  const all = [
    ...structured.entities.map((item) => ({
      kind: 'entity' as const,
      id: item.entityId,
      label: item.label || item.type || item.entityId,
      evidence: item.evidence,
    })),
    ...structured.instruments.map((item) => ({
      kind: 'instrument' as const,
      id: item.instrumentId,
      label: item.name || item.instrumentId,
      evidence: item.evidence,
    })),
    ...structured.assets.map((item) => ({
      kind: 'asset' as const,
      id: item.assetId,
      label: item.name || item.assetId,
      evidence: item.evidence,
    })),
  ];
  return all
    .flatMap(({ kind, id, label, evidence }) => {
      if (!evidence?.trackUrl || evidence.trackFirstSeenSeconds === null || evidence.trackLastSeenSeconds === null) return [];
      return [
        {
          kind,
          id,
          label,
          trackUrl: evidence.trackUrl,
          firstSeconds: evidence.trackFirstSeenSeconds,
          lastSeconds: evidence.trackLastSeenSeconds,
        },
      ];
    })
    .map((item, index) => ({ ...item, colour: trackColour(index) }));
}

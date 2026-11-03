// SPDX-License-Identifier: Apache-2.0

// Pure rules for drawing a rectangle on a video frame: a drag becomes a crop
// region for the crop route, and the same rectangle is stored as a bounding box.

import { MIN_CROP_SIDE } from '@/lib/evidence/frame-request';
import type { FrameRegion } from '@/lib/evidence/frame-geometry';

export interface FramePoint {
  x: number;
  y: number;
}

const clamp01 = (value: number) => Math.min(1, Math.max(0, value));

/**
 * The rectangle between two drag points (fractions 0-1 of the frame), in either
 * direction and clipped to the frame. Null when it is thinner than the crop
 * minimum, which is a stray click rather than a selection.
 */
export function dragRegion(from: FramePoint, to: FramePoint): FrameRegion | null {
  const x0 = clamp01(Math.min(from.x, to.x));
  const x1 = clamp01(Math.max(from.x, to.x));
  const y0 = clamp01(Math.min(from.y, to.y));
  const y1 = clamp01(Math.max(from.y, to.y));
  const w = x1 - x0;
  const h = y1 - y0;
  if (w < MIN_CROP_SIDE || h < MIN_CROP_SIDE) return null;
  return { x: x0, y: y0, w, h };
}

/** A region as the stored bounding box: [ymin, xmin, ymax, xmax] rounded to the 0-1000 grid. */
export function regionBox(region: FrameRegion): number[] {
  const grid = (fraction: number) => Math.round(fraction * 1000);
  return [grid(region.y), grid(region.x), grid(region.y + region.h), grid(region.x + region.w)];
}

/** The seconds an entity is on screen, so a picked frame keeps its most-visible second inside them. */
export interface SecondsRange {
  first: number;
  last: number;
}

/**
 * The range a picked frame must stay in. An entity whose first and last second are both 0 carries
 * no real on-screen window (the model gave none), and pinning to it would leave a slider that
 * cannot move, so it falls back to the whole video.
 */
export function usableRange(range: SecondsRange | null): SecondsRange | null {
  return range !== null && range.first === 0 && range.last === 0 ? null : range;
}

/**
 * A picked second: never negative, never past the end of the video when its length is known,
 * and inside the given range when there is one (see usableRange).
 */
export function pickSeconds(seconds: number, durationSeconds: number | null, range: SecondsRange | null = null): number {
  const seen = usableRange(range);
  const floor = Math.max(seen?.first ?? 0, seconds);
  const ceiling = seen === null ? durationSeconds : Math.min(seen.last, durationSeconds ?? seen.last);
  return ceiling === null ? floor : Math.min(floor, ceiling);
}

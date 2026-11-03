// SPDX-License-Identifier: Apache-2.0

// The model behind the track editor (Edit mode, "Edit track"): a reviewer fixes an item's track with a handful of keyframes
// instead of a box per frame. A track is a start, a stop and keyframes (a box at a second, straight-line interpolation between
// them). Loading thins a saved track to a few keyframes; Done densifies it back
// to the plain {t, box} samples the viewer, the presence bar and search already read. Pure functions, no I/O, browser-safe;
// every edit returns a new track.

import type { TrackSample } from '@/modules/reports/domain/track';

/** [ymin, xmin, ymax, xmax] on 0-1000, as the track files store it. */
export type Box = [number, number, number, number];

export interface Keyframe {
  t: number;
  box: Box;
}

export interface EditorTrack {
  start: number;
  stop: number;
  /** Sorted by `t`. A keyframe outside start..stop is kept (and drawn dimmed): widening the range brings it back. */
  kfs: Keyframe[];
}

/** What the editor draws at a second, and why. `still` is a saved box shown where no track exists. */
export type BoxState = 'keyframe' | 'between' | 'held' | 'outside' | 'none' | 'still';

export interface BoxAt {
  box: Box | null;
  state: BoxState;
  /** Index into `kfs` for `keyframe`. */
  index?: number;
}

export interface StillBoxAt {
  t: number;
  box: Box;
}

/** The playhead and every edited time snap to this, in seconds. */
export const SNAP_SECONDS = 0.1;
/** A box smaller than this on either side (0-1000 grid) is not a box. */
export const MIN_BOX_SIZE = 20;
/** The nearest the start and stop grips may come. */
export const MIN_TRACK_SECONDS = 0.5;
/** A drag on the timeline shorter than this in time is a click: it selects and moves nothing. */
export const DELIBERATE_DRAG_SECONDS = 0.2;
/** A keyframe is "at" a second when within this. */
export const KEYFRAME_MATCH_SECONDS = 0.05;
/** Samples written per second of track. */
export const DENSIFY_PER_SECOND = 4;
/** Longest list Save accepts (REVIEWER_SCHEMA). */
export const MAX_SAMPLES = 2000;
/** Thinning tolerance on the 0-1000 grid: a sample further than this from the line between keyframes becomes a keyframe. */
export const THIN_TOLERANCE = 8;
/** How long a fresh track is, from the second its first box is drawn at. */
export const NEW_TRACK_SECONDS = 2;

const EPSILON = 1e-6;

export const snapTenth = (seconds: number): number => +(Math.round(seconds / SNAP_SECONDS) * SNAP_SECONDS).toFixed(1);
const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));
const round3 = (value: number): number => +value.toFixed(3);

/** A box on the grid: each side rounded and kept in 0-1000, min before max. */
export function clampBox(box: readonly number[]): Box {
  const [a, b, c, d] = box.map((value) => clamp(Math.round(value), 0, 1000));
  return [Math.min(a, c), Math.min(b, d), Math.max(a, c), Math.max(b, d)];
}

/** Whether a box is big enough to be one (both sides at least MIN_BOX_SIZE). */
export const isUsableBox = (box: readonly number[]): boolean => box[2] - box[0] >= MIN_BOX_SIZE && box[3] - box[1] >= MIN_BOX_SIZE;

const lerpBox = (a: Box, b: Box, f: number): Box => a.map((value, i) => value + (b[i] - value) * f) as Box;

/**
 * The box at a second and which of five states it is in: on a `keyframe`, `between` two of them (straight line), `held` from
 * the nearest keyframe (before the first or after the last), `outside` start..stop (no
 * box) or with no track at all (`none`, or `still` at the saved still box's own second).
 */
export function boxAt(track: EditorTrack | null, still: StillBoxAt | null, seconds: number): BoxAt {
  if (!track) {
    return still && Math.abs(seconds - still.t) < KEYFRAME_MATCH_SECONDS
      ? { box: still.box, state: 'still' }
      : { box: null, state: 'none' };
  }
  if (seconds < track.start - EPSILON || seconds > track.stop + EPSILON) return { box: null, state: 'outside' };
  const { kfs } = track;
  if (!kfs.length) return { box: null, state: 'outside' };
  const onIndex = kfs.findIndex((kf) => Math.abs(kf.t - seconds) < KEYFRAME_MATCH_SECONDS);
  if (onIndex >= 0) return { box: [...kfs[onIndex].box], state: 'keyframe', index: onIndex };
  if (seconds < kfs[0].t) return { box: [...kfs[0].box], state: 'held' };
  const last = kfs[kfs.length - 1];
  if (seconds > last.t) return { box: [...last.box], state: 'held' };
  for (let i = 0; i < kfs.length - 1; i++) {
    if (seconds >= kfs[i].t && seconds <= kfs[i + 1].t) {
      return { box: lerpBox(kfs[i].box, kfs[i + 1].box, (seconds - kfs[i].t) / (kfs[i + 1].t - kfs[i].t)), state: 'between' };
    }
  }
  return { box: null, state: 'outside' };
}

/** The chip on the frame: what the current second is, and what a drag there does. */
export const STATE_CHIP: Record<BoxState, string> = {
  keyframe: 'Keyframe · drag the box to adjust it',
  between: 'Between keyframes · drag the box to add a keyframe here',
  held: 'Held from the nearest keyframe · drag the box to add a keyframe here',
  outside: 'Outside the track · draw a box to extend the track to here',
  still: 'Saved still box · drag it to start a track from it',
  none: 'No track yet',
};

/** Whether a drag on the frame draws a new box at this state (there is no box to move). */
export const drawsBox = (state: BoxState): boolean => state === 'outside' || state === 'none' || state === 'still';

/** Whether a keyframe is drawn dimmed: it lies outside start..stop. */
export const isDimmed = (track: EditorTrack, kf: Keyframe): boolean => kf.t < track.start - EPSILON || kf.t > track.stop + EPSILON;

/**
 * The track after a box is set at a second (moving, resizing or drawing a box): the keyframe there is updated or added,
 * the range widens to include the second. With no track, a new one starts at
 * the second and runs NEW_TRACK_SECONDS (to the clip end at most).
 */
export function setKeyframe(track: EditorTrack | null, seconds: number, box: readonly number[], duration: number | null): EditorTrack {
  const at = round3(Math.max(0, seconds));
  const fixed = clampBox(box);
  if (!track) {
    const stop = duration === null ? at + NEW_TRACK_SECONDS : Math.min(duration, at + NEW_TRACK_SECONDS);
    const start = stop - at < MIN_TRACK_SECONDS ? Math.max(0, round3(stop - MIN_TRACK_SECONDS)) : at;
    return { start, stop: round3(Math.max(stop, at)), kfs: [{ t: at, box: fixed }] };
  }
  const kfs = track.kfs.filter((kf) => Math.abs(kf.t - at) >= KEYFRAME_MATCH_SECONDS);
  kfs.push({ t: at, box: fixed });
  kfs.sort((a, b) => a.t - b.t);
  return {
    start: Math.min(track.start, at),
    stop: Math.max(track.stop, at),
    kfs,
  };
}

/** The index of the keyframe at a second, or -1. */
export const keyframeIndexAt = (track: EditorTrack, seconds: number): number =>
  track.kfs.findIndex((kf) => Math.abs(kf.t - seconds) < KEYFRAME_MATCH_SECONDS);

/** A keyframe moved in time: kept 0.1 s from its neighbours and inside start..stop. Unchanged when there is no room. */
export function moveKeyframe(track: EditorTrack, index: number, seconds: number): EditorTrack {
  const { kfs } = track;
  if (!kfs[index]) return track;
  const low = index > 0 ? kfs[index - 1].t + SNAP_SECONDS : track.start;
  const high = index < kfs.length - 1 ? kfs[index + 1].t - SNAP_SECONDS : track.stop;
  if (low > high + EPSILON) return track;
  const t = round3(clamp(seconds, low, high));
  return { ...track, kfs: kfs.map((kf, i) => (i === index ? { ...kf, t } : kf)) };
}

/** A keyframe deleted; null when it was the last one (the track goes with it). */
export function deleteKeyframe(track: EditorTrack, index: number): EditorTrack | null {
  if (!track.kfs[index]) return track;
  if (track.kfs.length === 1) return null;
  return { ...track, kfs: track.kfs.filter((_, i) => i !== index) };
}

/** The start grip set (kept MIN_TRACK_SECONDS before the stop). */
export function setStart(track: EditorTrack, seconds: number): EditorTrack {
  return { ...track, start: round3(clamp(seconds, 0, track.stop - MIN_TRACK_SECONDS)) };
}

/** The stop grip set (kept MIN_TRACK_SECONDS after the start, and inside the clip when its length is known). */
export function setStop(track: EditorTrack, seconds: number, duration: number | null): EditorTrack {
  const low = track.start + MIN_TRACK_SECONDS;
  return { ...track, stop: round3(clamp(seconds, low, duration === null ? Number.POSITIVE_INFINITY : Math.max(duration, low))) };
}

export type BoxHandle = 'move' | 'n' | 's' | 'e' | 'w' | 'nw' | 'ne' | 'sw' | 'se';

/** A box moved or resized by a drag of (dx, dy) on the 0-1000 grid from its state at the drag's start; kept in the frame, at least MIN_BOX_SIZE. */
export function dragBox(start: Box, handle: BoxHandle, dx: number, dy: number): Box {
  let [y0, x0, y1, x1] = start;
  if (handle === 'move') {
    const width = x1 - x0;
    const height = y1 - y0;
    x0 = clamp(x0 + dx, 0, 1000 - width);
    y0 = clamp(y0 + dy, 0, 1000 - height);
    x1 = x0 + width;
    y1 = y0 + height;
  } else {
    if (handle.includes('n')) y0 = clamp(y0 + dy, 0, y1 - MIN_BOX_SIZE);
    if (handle.includes('s')) y1 = clamp(y1 + dy, y0 + MIN_BOX_SIZE, 1000);
    if (handle.includes('w')) x0 = clamp(x0 + dx, 0, x1 - MIN_BOX_SIZE);
    if (handle.includes('e')) x1 = clamp(x1 + dx, x0 + MIN_BOX_SIZE, 1000);
  }
  return clampBox([y0, x0, y1, x1]);
}

/** The box drawn between two points of the frame (0-1000 each, [x, y]), kept in the frame. */
export const boxBetween = (from: readonly [number, number], to: readonly [number, number]): Box =>
  clampBox([Math.min(from[1], to[1]), Math.min(from[0], to[0]), Math.max(from[1], to[1]), Math.max(from[0], to[0])]);

/** The largest distance of any side between two boxes: how far a sample is from where a line puts it. */
const boxDistance = (a: readonly number[], b: readonly number[]): number => Math.max(...a.map((value, i) => Math.abs(value - b[i])));

/** Ramer-Douglas-Peucker over time: the samples that must stay for the line through them to keep within `tolerance` of the rest. */
function thinRun(run: readonly TrackSample[], tolerance: number): TrackSample[] {
  if (run.length <= 2) return [...run];
  const keep = new Set<number>([0, run.length - 1]);
  const stack: Array<[number, number]> = [[0, run.length - 1]];
  while (stack.length) {
    const [lo, hi] = stack.pop() as [number, number];
    const from = run[lo];
    const to = run[hi];
    let worst = -1;
    let worstAt = lo;
    for (let i = lo + 1; i < hi; i++) {
      const f = to.t === from.t ? 0 : (run[i].t - from.t) / (to.t - from.t);
      const error = boxDistance(run[i].box, lerpBox(from.box, to.box, f));
      if (error > worst) {
        worst = error;
        worstAt = i;
      }
    }
    if (worst > tolerance) {
      keep.add(worstAt);
      stack.push([lo, worstAt], [worstAt, hi]);
    }
  }
  return run.filter((_, i) => keep.has(i));
}

/**
 * A saved track as an editable one: start and stop from the first and last sample, and keyframes only where the box strays
 * more than `tolerance` from a straight line. A run of more than TRACK_MAX_GAP_SECONDS between samples (a gap the viewer
 * reads as "no box") loads as continuous: the editor has no removed stretch, so the box slides across it. Null for no samples.
 */
export function trackFromSamples(samples: readonly TrackSample[], tolerance = THIN_TOLERANCE): EditorTrack | null {
  if (!samples.length) return null;
  const sorted = [...samples].sort((a, b) => a.t - b.t);
  const kfs = thinRun(sorted, tolerance).map((sample): Keyframe => ({ t: round3(sample.t), box: clampBox(sample.box) }));
  return { start: round3(sorted[0].t), stop: round3(sorted[sorted.length - 1].t), kfs };
}

/**
 * The track as the plain samples the tracker writes: DENSIFY_PER_SECOND a second from start to stop (a sample exactly at the
 * start and at the stop), boxes on the grid. The step widens for a track so long it would pass
 * MAX_SAMPLES. Keyframes alone must not be written: two more than a second apart would read as a gap.
 */
export function densify(track: EditorTrack): TrackSample[] {
  const length = track.stop - track.start;
  const step = Math.max(1 / DENSIFY_PER_SECOND, length / (MAX_SAMPLES - 2));
  const times: number[] = [round3(track.start)];
  for (let i = 1; track.start + i * step < track.stop - EPSILON; i++) times.push(round3(track.start + i * step));
  if (length > EPSILON) times.push(round3(track.stop));
  const samples: TrackSample[] = [];
  for (const t of times) {
    const at = boxAt(track, null, t);
    if (at.box) samples.push({ t, box: clampBox(at.box) });
  }
  return samples;
}

/** The window the timeline shows: around the track (10 s either side, 20 s at least), or the whole clip. */
export function timelineWindow(track: EditorTrack | null, anchor: number, duration: number, whole = false): { from: number; to: number } {
  const MIN_WINDOW = 20;
  const PAD = 10;
  if (whole || duration <= MIN_WINDOW) return { from: 0, to: duration };
  const first = track ? Math.min(track.start, anchor) : anchor;
  const last = track ? Math.max(track.stop, anchor) : anchor;
  let from = first - PAD;
  let to = last + PAD;
  if (to - from < MIN_WINDOW) {
    const middle = (first + last) / 2;
    from = middle - MIN_WINDOW / 2;
    to = middle + MIN_WINDOW / 2;
  }
  if (from < 0) {
    to = Math.min(duration, to - from);
    from = 0;
  }
  if (to > duration) {
    from = Math.max(0, from - (to - duration));
    to = duration;
  }
  return { from: round3(from), to: round3(to) };
}

/** Ruler marks for a window: a tick each second when it is short, with a label every `label` seconds. */
export function rulerTicks(from: number, to: number): Array<{ t: number; label: boolean }> {
  const span = to - from;
  const [tick, label] = span <= 30 ? [1, 2] : span <= 90 ? [5, 10] : span <= 300 ? [10, 30] : span <= 900 ? [30, 60] : [60, 300];
  const ticks: Array<{ t: number; label: boolean }> = [];
  for (let t = Math.ceil(from / tick) * tick; t <= to + EPSILON; t += tick) ticks.push({ t, label: t % label === 0 });
  return ticks;
}

/** `m:ss.t`, the clock the editor shows. */
export function formatEditorClock(seconds: number): string {
  const whole = Math.floor(Math.max(0, seconds) + EPSILON);
  const tenth = Math.floor((Math.max(0, seconds) - whole + EPSILON) * 10) % 10;
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}.${tenth}`;
}

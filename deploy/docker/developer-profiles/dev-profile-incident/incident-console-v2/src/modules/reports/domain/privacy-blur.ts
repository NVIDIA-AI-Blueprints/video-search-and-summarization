// SPDX-License-Identifier: Apache-2.0

// Privacy blur (videos.blur_faces): the agent pixelates every head in every stored frame and crop of a run analysed with
// blur_faces (a whole-frame head detection, whatever tracking linked); the console blurs the clip the same way as it cuts
// it. Pure functions, no I/O, browser-safe: the head region of a person box (the same maths as the agent's
// utils/privacy_blur.py), whether a run was blurred, and the timed regions a clip blurs (the heads the agent found at
// sampled seconds of the clip window, every stored track per second, the recorded heads for the whole clip).

/** [ymin, xmin, ymax, xmax] normalised to 0-1000, like every evidence box. */
export type NormalisedBox = [number, number, number, number];

/** The head is the top 30 % of a person box, raised and widened by 10 % of the box (the agent's HEAD_FRACTION and HEAD_PADDING). */
export const HEAD_FRACTION = 0.3;
export const HEAD_PADDING = 0.1;
/** A head recorded at one second is grown by this much of its own size on every side when it stands for a whole clip, since people move. */
export const STATIC_HEAD_PADDING = 0.25;
/** At most this many regions go into one clip's filter graph; a busier clip has its closest regions merged. */
export const MAX_CLIP_REGIONS = 32;
/** The smallest region side kept for a clip, on 0-1000 (1 % of the frame). */
export const MIN_REGION = 10;

const clamp = (value: number) => Math.max(0, Math.min(1000, Math.round(value)));
const isBox = (value: unknown): value is NormalisedBox =>
  Array.isArray(value) &&
  value.length === 4 &&
  value.every((n) => typeof n === 'number' && Number.isFinite(n)) &&
  value[0] < value[2] &&
  value[1] < value[3];

/** The head region of a person box: its top HEAD_FRACTION, raised and widened by HEAD_PADDING of the box, clamped to the frame. */
export function headRegion(box: readonly number[]): NormalisedBox {
  const [ymin, xmin, ymax, xmax] = box;
  const height = ymax - ymin;
  const width = xmax - xmin;
  return [
    clamp(ymin - HEAD_PADDING * height),
    clamp(xmin - HEAD_PADDING * width),
    clamp(ymin + HEAD_FRACTION * height),
    clamp(xmax + HEAD_PADDING * width),
  ];
}

/** `box` grown by `fraction` of its own height and width on every side, clamped to the frame. */
export function padBox(box: readonly number[], fraction: number): NormalisedBox {
  const [ymin, xmin, ymax, xmax] = box;
  const dy = (ymax - ymin) * fraction;
  const dx = (xmax - xmin) * fraction;
  return [clamp(ymin - dy), clamp(xmin - dx), clamp(ymax + dy), clamp(xmax + dx)];
}

/** The smallest box holding every box given. */
function union(boxes: readonly NormalisedBox[]): NormalisedBox {
  return [
    Math.min(...boxes.map((b) => b[0])),
    Math.min(...boxes.map((b) => b[1])),
    Math.max(...boxes.map((b) => b[2])),
    Math.max(...boxes.map((b) => b[3])),
  ];
}

/** Whether a run's notes say the agent blurred its stored images (response.privacy.blur_faces true). */
export function runBlurredFaces(response: { privacy?: unknown } | null | undefined): boolean {
  const privacy = response?.privacy;
  return Boolean(privacy && typeof privacy === 'object' && (privacy as { blur_faces?: unknown }).blur_faces === true);
}

/** The head boxes the agent blurred in an item's frame (its evidence entry's blur_regions); null when the entry records none. */
export function recordedBlurRegions(value: unknown): NormalisedBox[] | null {
  if (!Array.isArray(value)) return null;
  return value.filter(isBox).map((box) => [...box] as NormalisedBox);
}

/** One region a clip blurs: a box, from `fromSeconds` to `toSeconds` of the clip (null: the whole clip). */
export interface ClipBlurRegion {
  box: NormalisedBox;
  fromSeconds: number | null;
  toSeconds: number | null;
}

/** One sample of a stored track ({t, box, score}); a score under 0 is a tracker-only box (no detection), no score reads as a detection. */
export interface BlurSample {
  t: number;
  box: NormalisedBox;
  score?: number;
}

/** A tracker-only box counts only within this long of the latest detection before it; later ones are the tracker drifting (the agent's PREDICTED_GRACE_SECONDS). */
export const PREDICTED_GRACE_SECONDS = 0.6;
/** A track sample belongs to a clip second when it is within this long of it, so a person moving across a second boundary stays covered. */
export const SECOND_MARGIN_SECONDS = 0.25;
/** Items whose second is this close to the clip count as showing the clip's people (their recorded heads and boxes blur it whole). */
export const CONTEXT_SECONDS = 1;

/** A stored track file read leniently, keeping each sample's score; null when it is not a list. */
export function parseBlurSamples(value: unknown): BlurSample[] | null {
  if (!Array.isArray(value)) return null;
  const samples: BlurSample[] = [];
  for (const raw of value) {
    if (!raw || typeof raw !== 'object') continue;
    const { t, box, score } = raw as { t?: unknown; box?: unknown; score?: unknown };
    if (typeof t !== 'number' || !Number.isFinite(t) || t < 0 || !isBox(box)) continue;
    samples.push({ t, box: [...box] as NormalisedBox, ...(typeof score === 'number' && Number.isFinite(score) ? { score } : {}) });
  }
  return samples.sort((a, b) => a.t - b.t);
}

/** The samples to trust: detections, and tracker-only boxes within PREDICTED_GRACE_SECONDS of the latest detection before them; the rest is drift. */
export function visibleSamples(samples: readonly BlurSample[]): BlurSample[] {
  const kept: BlurSample[] = [];
  let lastDetected: number | null = null;
  for (const sample of samples) {
    if (sample.score === undefined || sample.score >= 0) {
      lastDetected = sample.t;
      kept.push(sample);
    } else if (lastDetected !== null && sample.t - lastDetected <= PREDICTED_GRACE_SECONDS) {
      kept.push(sample);
    }
  }
  return kept;
}

/** The heads the agent found at sampled seconds of an item's clip window (`<itemId>-heads.json`), t in video seconds. */
export interface HeadSamples {
  /** The gap between samples, which each clip second reaches across so a head between two samples stays covered. */
  stepSeconds: number;
  samples: { t: number; boxes: NormalisedBox[] }[];
}

/** The widest reach a heads file may ask for on each side of a clip second; a corrupt step cannot blur or skip a whole clip. */
const MIN_STEP_SECONDS = 0.1;
const MAX_STEP_SECONDS = 2;
const DEFAULT_STEP_SECONDS = 0.5;

/** An item's stored heads file read leniently ({step_seconds, samples: [{t, boxes, fallback?}]}); null when it is not that shape. */
export function parseHeadSamples(value: unknown): HeadSamples | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const { step_seconds: step, samples: raw } = value as { step_seconds?: unknown; samples?: unknown };
  if (!Array.isArray(raw)) return null;
  const samples: HeadSamples['samples'] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const { t, boxes } = item as { t?: unknown; boxes?: unknown };
    if (typeof t !== 'number' || !Number.isFinite(t) || t < 0 || !Array.isArray(boxes)) continue;
    samples.push({ t, boxes: boxes.filter(isBox).map((box) => [...box] as NormalisedBox) });
  }
  const stepSeconds =
    typeof step === 'number' && Number.isFinite(step) ? Math.min(MAX_STEP_SECONDS, Math.max(MIN_STEP_SECONDS, step)) : DEFAULT_STEP_SECONDS;
  return { stepSeconds, samples: samples.sort((a, b) => a.t - b.t) };
}

export interface ClipBlurInput {
  /** The clip's start and length in video seconds. */
  window: { startSeconds: number; lengthSeconds: number };
  /** Every track the run stored for the clip's video (each matched entity's, not only the clip item's own). */
  tracks: readonly (readonly BlurSample[])[];
  /** The heads the agent blurred in the frames of the items near the clip (blur_regions); null when none of them recorded any. */
  recorded: readonly NormalisedBox[] | null;
  /** The located boxes of the people near the clip, whose heads stand in when their frame's heads were not recorded. */
  personBoxes: readonly (readonly number[])[];
  /** The heads found at sampled seconds of the clip window; null when the item stored none (an older version). */
  heads?: HeadSamples | null;
}

const round3 = (value: number) => Math.round(value * 1000) / 1000;

/** One track's head regions, one per second of the clip it has samples near, each the union of that track's heads in (and just around) the second. */
function trackRegions(track: readonly BlurSample[], { startSeconds, lengthSeconds }: ClipBlurInput['window']): ClipBlurRegion[] {
  const end = startSeconds + lengthSeconds;
  const inClip = visibleSamples(track).filter((sample) => sample.t >= startSeconds - 0.5 && sample.t <= end + 0.5);
  const regions: ClipBlurRegion[] = [];
  for (let second = 0; second < Math.ceil(lengthSeconds); second++) {
    const heads = inClip
      .filter((sample) => {
        const at = sample.t - startSeconds;
        return at >= second - SECOND_MARGIN_SECONDS && at < second + 1 + SECOND_MARGIN_SECONDS;
      })
      .map((sample) => headRegion(sample.box));
    if (heads.length) regions.push({ box: union(heads), fromSeconds: second, toSeconds: round3(Math.min(lengthSeconds, second + 1)) });
  }
  return regions;
}

const overlap = (a: NormalisedBox, b: NormalisedBox) => a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];

/** Boxes that overlap, directly or through others, become their union, so two people stay two regions and one head found twice stays one. */
function cluster(boxes: readonly NormalisedBox[]): NormalisedBox[] {
  const out = [...boxes];
  for (let merged = true; merged; ) {
    merged = false;
    for (let i = 0; i < out.length && !merged; i++) {
      for (let j = i + 1; j < out.length && !merged; j++) {
        if (!overlap(out[i], out[j])) continue;
        out[i] = union([out[i], out[j]]);
        out.splice(j, 1);
        merged = true;
      }
    }
  }
  return out;
}

/**
 * The heads the agent found in the clip window, one set of regions per clip second: every sample from one step before
 * the second to the end of it, so a head seen at any sample around the second is blurred for the whole second.
 */
function headRegions(heads: HeadSamples, { startSeconds, lengthSeconds }: ClipBlurInput['window']): ClipBlurRegion[] {
  const regions: ClipBlurRegion[] = [];
  for (let second = 0; second < Math.ceil(lengthSeconds); second++) {
    const boxes = heads.samples
      .filter((sample) => {
        const at = sample.t - startSeconds;
        return at >= second - heads.stepSeconds - 1e-6 && at < second + 1 + 1e-6;
      })
      .flatMap((sample) => sample.boxes);
    for (const box of cluster(boxes)) {
      regions.push({ box, fromSeconds: second, toSeconds: round3(Math.min(lengthSeconds, second + 1)) });
    }
  }
  return regions;
}

const area = ([ymin, xmin, ymax, xmax]: NormalisedBox) => (ymax - ymin) * (xmax - xmin);
const sameSpan = (a: ClipBlurRegion, b: ClipBlurRegion) => a.fromSeconds === b.fromSeconds && a.toSeconds === b.toSeconds;

/** At most MAX_CLIP_REGIONS regions: while there are more, the two of one time span whose union is smallest become that union, so no head ever drops out. */
function limitRegions(regions: ClipBlurRegion[]): ClipBlurRegion[] {
  const out = [...regions];
  while (out.length > MAX_CLIP_REGIONS) {
    let best: [number, number] | null = null;
    let bestArea = Number.POSITIVE_INFINITY;
    for (let i = 0; i < out.length; i++) {
      for (let j = i + 1; j < out.length; j++) {
        if (!sameSpan(out[i], out[j])) continue;
        const merged = area(union([out[i].box, out[j].box]));
        if (merged < bestArea) {
          bestArea = merged;
          best = [i, j];
        }
      }
    }
    if (!best) return [{ box: union(out.map((region) => region.box)), fromSeconds: null, toSeconds: null }];
    const [i, j] = best;
    out[i] = { ...out[i], box: union([out[i].box, out[j].box]) };
    out.splice(j, 1);
  }
  return out;
}

/**
 * The regions an evidence clip blurs, in clip seconds; null when nothing is known about the heads it shows (then no
 * blurred clip can be cut). Per second of the clip, the heads the agent found at the sampled seconds around it (a
 * whole-frame detection, whatever tracking linked; each cluster of overlapping heads is one region, so people stay
 * apart), and the head regions of every stored track (each person's own, so a
 * person who moves or stands up stays covered, and so does a bystander the run matched); for the whole clip, each head
 * the agent blurred in the frames of the items near it (padded, since people move), and the head of a located person
 * whose frame's heads were not recorded (an older version). A track's drifting tracker-only boxes are not blurred. An
 * item with no stored heads (`heads` null: an older version) keeps the tracks and recorded heads alone. An empty list
 * means the agent recorded no head near the clip. A clip with more regions than MAX_CLIP_REGIONS has the
 * closest ones merged.
 */
export function clipBlurRegions(input: ClipBlurInput): ClipBlurRegion[] | null {
  const regions: ClipBlurRegion[] = [
    ...(input.heads ? headRegions(input.heads, input.window) : []),
    ...input.tracks.flatMap((track) => trackRegions(track, input.window)),
  ];
  const heads: NormalisedBox[] = [
    ...(input.recorded ?? []).map((box) => padBox(box, STATIC_HEAD_PADDING)),
    ...input.personBoxes.filter((box) => isBox([...box])).map((box) => padBox(headRegion(box), STATIC_HEAD_PADDING)),
  ];
  const seen = new Set<string>();
  for (const box of heads) {
    if (seen.has(box.join())) continue;
    seen.add(box.join());
    regions.push({ box, fromSeconds: null, toSeconds: null });
  }
  if (!regions.length && !input.recorded && !input.heads) return null;
  // A sliver of a box rounds to a zero-pixel crop, which ffmpeg reads as the whole frame: such regions are dropped.
  return limitRegions(regions.filter(({ box }) => box[2] - box[0] >= MIN_REGION && box[3] - box[1] >= MIN_REGION));
}

const fraction = (value: number) => (value / 1000).toFixed(4);

/**
 * The ffmpeg filter graph that scales the clip (`scale`, the plain clip's -vf) and box-blurs each region over its time
 * span: per region the frame is split, the region cropped and blurred, and laid back over itself with
 * enable='between(t,a,b)' (no enable for a whole-clip region). Positions are fractions of the scaled frame, rounded to
 * even pixels for yuv420p. The output pad is [blurred].
 */
export function clipBlurFilter(scale: string, regions: readonly ClipBlurRegion[]): string {
  const parts = [`[0:v:0]${scale}[v0]`];
  regions.forEach((region, i) => {
    const [ymin, xmin, ymax, xmax] = region.box;
    const x = (width: string) => `'2*trunc(${width}*${fraction(xmin)}/2)'`;
    const y = (height: string) => `'2*trunc(${height}*${fraction(ymin)}/2)'`;
    const w = `'2*trunc(iw*${fraction(xmax - xmin)}/2)'`;
    const h = `'2*trunc(ih*${fraction(ymax - ymin)}/2)'`;
    const enable = region.fromSeconds === null ? '' : `:enable='between(t,${region.fromSeconds},${region.toSeconds})'`;
    parts.push(
      `[v${i}]split[m${i}][c${i}]`,
      `[c${i}]crop=w=${w}:h=${h}:x=${x('iw')}:y=${y('ih')},boxblur=luma_radius='max(0,min(w,h)/2-1)':luma_power=3:chroma_radius='max(0,min(cw,ch)/2-1)':chroma_power=3[k${i}]`,
      `[m${i}][k${i}]overlay=x=${x('main_w')}:y=${y('main_h')}${enable}[v${i + 1}]`,
    );
  });
  parts.push(`[v${regions.length}]null[blurred]`);
  return parts.join(';');
}

/** What the face-blur toggle shows while its re-analysis job waits or runs. */
export function privacyJobLabel(job: { status: string }): string {
  return job.status === 'queued' ? 'Re-analysis queued…' : job.status === 'running' ? 'Re-analysing…' : 'Finishing…';
}

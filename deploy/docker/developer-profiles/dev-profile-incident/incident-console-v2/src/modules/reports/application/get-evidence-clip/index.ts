// SPDX-License-Identifier: Apache-2.0

// Get evidence clip: a signed URL for a few seconds of the video around one item's second. The first request cuts the clip
// from the video's R2 copy and stores it beside the item's evidence; later ones sign the stored object. The item, its second
// and its folder come from the stored revision's evidence entry, never from the browser.
//
// Face blur: when the video's toggle is on, or the revision's run was analysed with blur_faces, the clip is cut with the
// heads blurred (privacy-blur.ts clipBlurRegions: per second from the heads the agent found at sampled seconds of the
// clip window (the item's own <itemId>-heads.json, whatever tracking linked), from every track the revision stored for
// the video, not only the item's own, and for the whole clip the heads the agent blurred in the frames of the items near
// it, else the heads of the located people near it) and cached under the separate -clip-blur.mp4 key. An item with no
// heads file (an older version) keeps the tracks and the recorded heads.

import type { IncidentRepository } from '@/modules/reports/domain/IncidentRepository';
import { ClipError, type ClipWindow, clipCentreSeconds, clipWindow, evidenceClipKey } from '@/modules/reports/domain/evidence-clip';
import {
  type BlurSample,
  type ClipBlurRegion,
  CONTEXT_SECONDS,
  clipBlurRegions,
  type HeadSamples,
  type NormalisedBox,
  parseBlurSamples,
  parseHeadSamples,
  recordedBlurRegions,
  runBlurredFaces,
} from '@/modules/reports/domain/privacy-blur';
import type { EvidenceItemType } from '@/modules/reports/domain/revision';
import { isValidR2Key } from '@/lib/r2/key';

/** Reads the source video and keeps cut clips; the route supplies R2 and ffmpeg, tests a fake. */
export interface ClipStore {
  /** Whether an object is stored under `key`. */
  exists(key: string): Promise<boolean>;
  /** The video's length in seconds; null when it cannot be read. Throws when the video object is missing. */
  durationSeconds(videoKey: string): Promise<number | null>;
  /** The clip of the video as MP4 bytes, with `regions` blurred; throws ClipError when ffmpeg is missing or fails. */
  cut(videoKey: string, window: ClipWindow, regions?: readonly ClipBlurRegion[]): Promise<Uint8Array>;
  /** A stored JSON object (an item's track); throws when it cannot be read. */
  readJson(key: string): Promise<unknown>;
  /** Stores the clip; a key that another request stored first is not an error. */
  put(key: string, bytes: Uint8Array): Promise<void>;
  /** A short-lived URL the browser plays. */
  sign(key: string): Promise<string>;
}

export interface EvidenceClipRequest {
  videoId: string;
  revisionId: number;
  itemType: EvidenceItemType;
  itemId: string;
}

/** Clips being cut in this process, by key: a second request for the same clip waits for the first cut instead of cutting again. */
const cutting = new Map<string, Promise<void>>();

/** What a blurred clip is built from; null for a clip cut as it is. */
interface BlurSource {
  tracks: BlurSample[][];
  recorded: NormalisedBox[] | null;
  personBoxes: number[][];
  heads: HeadSamples | null;
}

async function ensureClip(
  store: ClipStore,
  key: string,
  videoKey: string,
  centreSeconds: number,
  blurSource: (window: ClipWindow) => Promise<BlurSource | null>,
): Promise<void> {
  if (await store.exists(key)) return;
  let duration: number | null;
  try {
    duration = await store.durationSeconds(videoKey);
  } catch {
    throw new ClipError(409, 'The video file is missing from storage, so no clip can be cut');
  }
  if (duration !== null && centreSeconds >= duration) {
    throw new ClipError(422, `The item's second ${centreSeconds} s is outside the video, which is ${+duration.toFixed(3)} s long`);
  }
  const window = clipWindow(centreSeconds, duration);
  const blur = await blurSource(window);
  if (!blur) {
    await store.put(key, await store.cut(videoKey, window));
    return;
  }
  const regions = clipBlurRegions({ window, ...blur });
  if (regions === null) {
    throw new ClipError(422, 'No head positions are recorded for this item, so its clip cannot be cut with the faces blurred');
  }
  await store.put(key, await store.cut(videoKey, window, regions));
}

const TRACK_KEY = /^evidence\/.+-track\.json$/;
const HEADS_KEY = /^evidence\/.+-heads\.json$/;

/** The signed URL of the item's clip, cutting and storing it first when it is not stored yet. */
export async function getEvidenceClip(incidents: IncidentRepository, store: ClipStore, request: EvidenceClipRequest): Promise<string> {
  const record = await incidents.getRevision(request.videoId, request.revisionId);
  if (!record || record.revision.revisionId !== request.revisionId) throw new ClipError(404, 'Report version not found');
  const videoKey = record.video.storageKey;
  if (!isValidR2Key(videoKey)) throw new ClipError(409, 'This video has no stored file to cut a clip from');
  const entry = record.evidence.find((candidate) => candidate.item_type === request.itemType && candidate.item_id === request.itemId);
  if (!entry) throw new ClipError(404, 'This item has no evidence to cut a clip for');
  const seconds = clipCentreSeconds({
    boxSeconds: typeof entry.box_seconds === 'number' ? entry.box_seconds : null,
    timestampSeconds: entry.timestamp_seconds,
  });
  if (seconds === null) throw new ClipError(422, 'No second was recorded for this item, so it has no clip');
  const response = record.modelRun?.notes.kind === 'contract' ? record.modelRun.notes.response : null;
  const blurred = record.video.blurFaces === true || runBlurredFaces(response);
  const key = evidenceClipKey([entry.frame as string | null, entry.image, entry.track as string | null], videoKey, request.itemId, blurred);
  if (!key) throw new ClipError(422, 'This item has no stored evidence to keep a clip beside');

  let pending = cutting.get(key);
  if (!pending) {
    const blur = async (window: ClipWindow): Promise<BlurSource | null> => {
      if (!blurred) return null;
      const from = window.startSeconds - CONTEXT_SECONDS;
      const to = window.startSeconds + window.lengthSeconds + CONTEXT_SECONDS;
      const secondOf = (candidate: (typeof record.evidence)[number]) => {
        const at = candidate.box_seconds ?? candidate.timestamp_seconds;
        return typeof at === 'number' ? at : null;
      };
      const near = record.evidence.filter((candidate) => {
        const at = secondOf(candidate);
        return at !== null && at >= from && at <= to;
      });
      // Every track the revision stored that can show in the clip, not only the item's own: a person who moves, stands up
      // or walks past stays covered. A track whose recorded span lies outside the clip is not read.
      const trackKeys = new Set<string>();
      for (const candidate of record.evidence) {
        // Only a person's track is a track of heads; an instrument's or asset's (object tracker) is a box on the object.
        if (candidate.item_type !== 'entity') continue;
        const key = candidate.track;
        if (typeof key !== 'string' || !TRACK_KEY.test(key)) continue;
        const first = candidate.track_first_seen_seconds;
        const last = candidate.track_last_seen_seconds;
        if (typeof first === 'number' && typeof last === 'number' && (last < from || first > to)) continue;
        trackKeys.add(key);
      }
      let tracks: BlurSample[][];
      try {
        tracks = (await Promise.all([...trackKeys].map(async (key) => parseBlurSamples(await store.readJson(key))))).filter(
          (samples): samples is BlurSample[] => samples !== null,
        );
      } catch {
        throw new ClipError(502, 'A track of this video could not be read, so the clip cannot be cut with the faces blurred');
      }
      let heads: HeadSamples | null = null;
      if (typeof entry.heads === 'string' && HEADS_KEY.test(entry.heads)) {
        try {
          heads = parseHeadSamples(await store.readJson(entry.heads));
        } catch {
          throw new ClipError(502, "This item's head positions could not be read, so the clip cannot be cut with the faces blurred");
        }
      }
      const recordedLists = near.map((candidate) => recordedBlurRegions(candidate.blur_regions)).filter((list) => list !== null);
      const personBoxes = near
        .filter(
          (candidate) =>
            candidate.item_type === 'entity' &&
            candidate.located !== false &&
            Array.isArray(candidate.bounding_box) &&
            recordedBlurRegions(candidate.blur_regions) === null,
        )
        .map((candidate) => candidate.bounding_box as number[]);
      return {
        tracks,
        // Null (nothing recorded: an older version) only when no item near the clip, and the item itself, recorded heads.
        recorded: recordedLists.length ? recordedLists.flat() : null,
        personBoxes,
        heads,
      };
    };
    pending = ensureClip(store, key, videoKey, seconds, blur).finally(() => cutting.delete(key));
    cutting.set(key, pending);
  }
  await pending;
  return store.sign(key);
}

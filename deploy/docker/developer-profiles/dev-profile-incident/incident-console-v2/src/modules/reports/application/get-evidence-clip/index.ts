// SPDX-License-Identifier: Apache-2.0

// Get evidence clip: a signed URL for a few seconds of the video around one item's second. The first request cuts the clip
// from the video's R2 copy and stores it beside the item's evidence; later ones sign the stored object. The item, its second
// and its folder come from the stored revision's evidence entry, never from the browser.

import type { IncidentRepository } from '@/modules/reports/domain/IncidentRepository';
import { ClipError, type ClipWindow, clipCentreSeconds, clipWindow, evidenceClipKey } from '@/modules/reports/domain/evidence-clip';
import type { EvidenceItemType } from '@/modules/reports/domain/revision';
import { isValidR2Key } from '@/lib/r2/key';

/** Reads the source video and keeps cut clips; the route supplies R2 and ffmpeg, tests a fake. */
export interface ClipStore {
  /** Whether an object is stored under `key`. */
  exists(key: string): Promise<boolean>;
  /** The video's length in seconds; null when it cannot be read. Throws when the video object is missing. */
  durationSeconds(videoKey: string): Promise<number | null>;
  /** The clip of the video as MP4 bytes; throws ClipError when ffmpeg is missing or fails. */
  cut(videoKey: string, window: ClipWindow): Promise<Uint8Array>;
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

async function ensureClip(store: ClipStore, key: string, videoKey: string, centreSeconds: number): Promise<void> {
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
  await store.put(key, await store.cut(videoKey, window));
}

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
  const key = evidenceClipKey([entry.frame as string | null, entry.image, entry.track as string | null], videoKey, request.itemId);
  if (!key) throw new ClipError(422, 'This item has no stored evidence to keep a clip beside');

  let pending = cutting.get(key);
  if (!pending) {
    pending = ensureClip(store, key, videoKey, seconds).finally(() => cutting.delete(key));
    cutting.set(key, pending);
  }
  await pending;
  return store.sign(key);
}

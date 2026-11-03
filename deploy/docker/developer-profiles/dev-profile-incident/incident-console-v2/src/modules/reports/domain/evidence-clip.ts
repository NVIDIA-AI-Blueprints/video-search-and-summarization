// SPDX-License-Identifier: Apache-2.0

// An evidence clip: a few seconds of the video around an item's second, cut on its first play and kept in R2 beside the
// item's evidence (<folder>/<itemId>-clip.mp4, where <folder> is the run-<n>/ or edit-<n>/ folder of its frame, image or
// track). Pure functions, no I/O, browser-safe: the window, the cache key and the pop-up button's states.

import type { EvidenceItemType } from '@/modules/reports/domain/revision';
import { isEvidenceKeyOfVideo } from '@/modules/reports/domain/revision';

/** The clip's length when the video is long enough; a shorter video is clipped whole. */
export const CLIP_SECONDS = 4;

export interface ClipWindow {
  startSeconds: number;
  lengthSeconds: number;
}

const round3 = (value: number) => Math.round(value * 1000) / 1000;

/**
 * CLIP_SECONDS centred on `centreSeconds`, shifted to stay inside a video of `durationSeconds` (null when its length is
 * unknown: then only the start is clamped, at 0). A video shorter than CLIP_SECONDS is clipped whole.
 */
export function clipWindow(centreSeconds: number, durationSeconds: number | null): ClipWindow {
  const length = durationSeconds === null ? CLIP_SECONDS : Math.min(CLIP_SECONDS, durationSeconds);
  const latestStart = durationSeconds === null ? Number.POSITIVE_INFINITY : durationSeconds - length;
  const start = Math.max(0, Math.min(centreSeconds - CLIP_SECONDS / 2, latestStart));
  return { startSeconds: round3(start), lengthSeconds: round3(length) };
}

/** The second a clip is centred on: the second the box was found at, else the entry's own second; null when neither is recorded. */
export function clipCentreSeconds(evidence: { boxSeconds: number | null; timestampSeconds: number | null }): number | null {
  const seconds = evidence.boxSeconds ?? evidence.timestampSeconds;
  return typeof seconds === 'number' && Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

const ITEM_ID = /^[A-Za-z0-9_-]{1,64}$/;

/**
 * The R2 key the item's clip is cached under: `<folder>/<itemId>-clip.mp4` in the
 * folder of the item's first stored evidence object (frame, image, then track) under the video's evidence folder. null
 * when the item stores none, so it has nowhere to cache a clip.
 */
export function evidenceClipKey(storedKeys: ReadonlyArray<string | null | undefined>, videoKey: string, itemId: string): string | null {
  if (!ITEM_ID.test(itemId)) return null;
  for (const key of storedKeys) {
    if (typeof key !== 'string' || !isEvidenceKeyOfVideo(key, videoKey)) continue;
    const slash = key.lastIndexOf('/');
    if (slash <= 0) continue;
    return `${key.slice(0, slash + 1)}${itemId}-clip.mp4`;
  }
  return null;
}

/** The console route that cuts (on first play) and signs one item's clip. */
export function evidenceClipUrl(videoId: string, revisionId: number, itemType: EvidenceItemType, itemId: string): string {
  const query = new URLSearchParams({ revision: String(revisionId), type: itemType, item: itemId });
  return `/api/reports/${encodeURIComponent(videoId)}/evidence-clip?${query}`;
}

/** The pop-up's clip: not asked for yet, being cut, playing the signed URL, or failed with the route's message. */
export type ClipState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; url: string }
  | { status: 'error'; message: string };

/** What the Play clip button shows in each state; once the clip plays, the button is gone and the player stands in. */
export function clipButton(state: ClipState): { label: string; disabled: boolean; visible: boolean } {
  switch (state.status) {
    case 'idle':
      return { label: 'Play clip', disabled: false, visible: true };
    case 'loading':
      return { label: 'Cutting clip…', disabled: true, visible: true };
    case 'error':
      return { label: 'Try again', disabled: false, visible: true };
    case 'ready':
      return { label: 'Play clip', disabled: true, visible: false };
  }
}

/** A clip request refused or failed, with the HTTP status the route answers and a message the pop-up shows. */
export class ClipError extends Error {
  readonly status: 400 | 404 | 409 | 422 | 502 | 503;

  constructor(status: ClipError['status'], message: string) {
    super(message);
    this.name = 'ClipError';
    this.status = status;
  }
}

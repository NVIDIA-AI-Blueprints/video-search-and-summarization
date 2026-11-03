// SPDX-License-Identifier: Apache-2.0

// Store reviewer items: for each item of a saved edit that came from the box tool (or was re-tracked), cut the crop and the
// whole frame at the first-seen second, store them and the track file under `edit-<N>/`, and answer the evidence entry the
// new revision holds for it. The reviewer's own box is the box, so nothing is detected here.

import { boxRegion } from '@/lib/evidence/frame-geometry';
import type { StructuredEdit } from '@/modules/reports/domain/edit-draft';
import { reviewerEntryFields, type ReviewerPayload } from '@/modules/reports/domain/reviewer-item';
import {
  type EvidenceEntry,
  type EvidenceItemType,
  reviewerFrameKey,
  reviewerTrackKey,
  uploadedEvidenceKey,
} from '@/modules/reports/domain/revision';
import { StoreRejection } from '@/modules/shared/domain/common';
import type { ReviewerEvidenceStore } from '@/modules/reports/application/types';

const REPLACED_FIELDS = [
  'image_gap',
  'missing',
  'track',
  'track_first_seen_seconds',
  'track_last_seen_seconds',
  'track_source',
  'track_partial',
];

interface ReviewerItem {
  type: EvidenceItemType;
  itemId: string;
  sourceId: string | null;
  reviewer: ReviewerPayload;
}

/** The edit's items that carry reviewer data, by their final IDs. */
export function reviewerItems(edit: StructuredEdit): ReviewerItem[] {
  const pick = <T extends { source_id: string | null; reviewer?: ReviewerPayload | null }>(
    type: EvidenceItemType,
    items: T[],
    idOf: (item: T) => string,
  ): ReviewerItem[] =>
    items.flatMap((item) => (item.reviewer ? [{ type, itemId: idOf(item), sourceId: item.source_id, reviewer: item.reviewer }] : []));
  return [
    ...pick('entity', edit.entities, (item) => item.entity_id),
    ...pick('instrument', edit.instruments, (item) => item.instrument_id),
    ...pick('asset', edit.assets, (item) => item.asset_id),
  ];
}

/** The first `edit-<n>` none of whose keys exist yet, so no object is ever overwritten. */
async function freeEditNumber(store: ReviewerEvidenceStore, keysAt: (n: number) => string[]): Promise<number> {
  for (let n = 1; ; n += 1) {
    const taken = await Promise.all(keysAt(n).map((key) => store.exists(key)));
    if (!taken.some(Boolean)) return n;
  }
}

/**
 * Stores every reviewer item of the edit and answers its evidence entry, keyed `<type>:<itemId>`. `parentEvidence` lets a
 * re-tracked item keep what its source entry says apart from the image, frame and track.
 */
export async function storeReviewerItems(
  store: ReviewerEvidenceStore,
  videoKey: string,
  edit: StructuredEdit,
  parentEvidence: EvidenceEntry[],
): Promise<Map<string, EvidenceEntry>> {
  const items = reviewerItems(edit);
  const entries = new Map<string, EvidenceEntry>();
  if (!items.length) return entries;
  const editNumber = await freeEditNumber(store, (n) =>
    items.flatMap((item) => [
      uploadedEvidenceKey(videoKey, n, item.itemId),
      reviewerFrameKey(videoKey, n, item.itemId),
      reviewerTrackKey(videoKey, n, item.itemId),
    ]),
  );
  for (const { type, itemId, sourceId, reviewer } of items) {
    const first = reviewer.track?.samples[0];
    const seconds = first ? first.t : reviewer.still_seconds;
    const box = first ? first.box : reviewer.still_box;
    const region = boxRegion(box);
    if (seconds === null || seconds === undefined || !region)
      throw new StoreRejection('invalid', `${itemId}: a reviewer item needs a track, or a still second and box`);
    const [crop, frame] = await Promise.all([store.frame(videoKey, seconds, region), store.frame(videoKey, seconds, null)]);
    const keys = {
      image: uploadedEvidenceKey(videoKey, editNumber, itemId),
      frame: reviewerFrameKey(videoKey, editNumber, itemId),
      track: reviewer.track ? reviewerTrackKey(videoKey, editNumber, itemId) : null,
    };
    await store.put(keys.image, crop, 'image/jpeg');
    await store.put(keys.frame, frame, 'image/jpeg');
    if (reviewer.track && keys.track) await store.put(keys.track, Buffer.from(JSON.stringify(reviewer.track)), 'application/json');
    const base = sourceId ? parentEvidence.find((entry) => entry.item_type === type && entry.item_id === sourceId) : undefined;
    const kept = Object.fromEntries(Object.entries(base ?? {}).filter(([key]) => !REPLACED_FIELDS.includes(key)));
    const entry = { ...kept, item_type: type, item_id: itemId, ...reviewerEntryFields(reviewer, keys) } as EvidenceEntry;
    // A re-tracked item keeps the source its entry had (an AI entry has none); only a box the reviewer drew is `reviewer`.
    if (base) {
      if (base.source === undefined) delete entry.source;
      else entry.source = base.source;
    }
    entries.set(`${type}:${itemId}`, entry);
  }
  return entries;
}

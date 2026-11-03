// SPDX-License-Identifier: Apache-2.0

// Get evidence track: one item's track samples for the report player's overlay. The key comes from the stored revision's
// evidence entry, never from the browser, so the route can only read a track the revision itself names.

import type { ReadStoredJson } from '@/modules/reports/application/types';
import type { IncidentRepository } from '@/modules/reports/domain/IncidentRepository';
import type { EvidenceItemType } from '@/modules/reports/domain/revision';
import { parseTrackSamples, type TrackSample } from '@/modules/reports/domain/track';

const TRACK_KEY = /^evidence\/.+-track\.json$/;

/**
 * The samples of the track the revision's evidence entry names; null when the revision, the entry or its track is missing.
 * A stored file that is neither a list of samples nor a reviewer object with `samples` answers an empty list (the overlay draws nothing for it).
 */
export async function getEvidenceTrack(
  incidents: IncidentRepository,
  read: ReadStoredJson,
  request: { videoId: string; revisionId: number; itemType: EvidenceItemType; itemId: string },
): Promise<TrackSample[] | null> {
  const record = await incidents.getRevision(request.videoId, request.revisionId);
  if (!record || record.revision.revisionId !== request.revisionId) return null;
  const entry = record.evidence.find((candidate) => candidate.item_type === request.itemType && candidate.item_id === request.itemId);
  const key = entry?.track;
  if (typeof key !== 'string' || !TRACK_KEY.test(key)) return null;
  return parseTrackSamples(await read(key)) ?? [];
}

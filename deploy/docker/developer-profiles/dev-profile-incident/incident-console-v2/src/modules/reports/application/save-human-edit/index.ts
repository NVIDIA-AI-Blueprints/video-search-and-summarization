// SPDX-License-Identifier: Apache-2.0

// Save human edit: a new human revision from the form's edit, parented on the
// revision the reviewer opened. When the video has no official revision the new
// revision is the searchable one, so its crops are embedded first and written in
// the same call as the revision: a failed embedding writes nothing.

import type { DbAdapters } from '@/modules/shared/domain/adapters';
import type { RevisionRecord } from '@/modules/reports/domain/IncidentRepository';
import type { StructuredEdit } from '@/modules/reports/domain/edit-draft';
import type { EmbedEvidence, ReviewerEvidenceStore } from '@/modules/reports/application/types';
import { storeReviewerItems } from '@/modules/reports/application/store-reviewer-items';
import { StoreRejection } from '@/modules/shared/domain/common';
import { missingItemFields } from '@/modules/contract/domain/validate';
import {
  humanRevisionContent,
  isEvidenceKeyOfVideo,
  officialCrops,
  type EvidenceEntry,
  type OfficialCrop,
  type ReportPayload,
  type RevisionSummary,
} from '@/modules/reports/domain/revision';

/** The save was refused because the new revision's crops could not be embedded. Nothing was written. */
export class SearchableStepError extends Error {
  constructor(reason: string) {
    super(`The new version was not saved: its evidence could not be made searchable (${reason}). Try Save again.`);
    this.name = 'SearchableStepError';
  }
}

function hasReviewerItems(edit: StructuredEdit): boolean {
  return [...edit.entities, ...edit.instruments, ...edit.assets].some((item) => item.reviewer);
}

/** An edit's item images must be ones the opened revision already used or new uploads for this video. */
function checkImages(parent: RevisionRecord, edit: StructuredEdit, videoKey: string | null) {
  const known = new Set(parent.evidence.flatMap((entry) => (entry.image ? [entry.image] : [])));
  for (const item of [...edit.entities, ...edit.instruments, ...edit.assets]) {
    if (item.image && !known.has(item.image) && !(videoKey !== null && isEvidenceKeyOfVideo(item.image, videoKey))) {
      throw new StoreRejection('invalid', `An evidence image is not one of this video's images: ${item.image}`);
    }
  }
}

/**
 * The crops the new revision will be searchable with: embedded here, before the revision is written. An
 * embedding failure stops the save, so no revision is written without its crops.
 */
async function searchableCrops(payload: ReportPayload, evidence: EvidenceEntry[], embed: EmbedEvidence): Promise<OfficialCrop[]> {
  const withImages = evidence.filter((entry) => entry.image);
  if (!withImages.length) return [];
  try {
    return officialCrops(payload, evidence, await embed(withImages));
  } catch (error) {
    throw new SearchableStepError(error instanceof Error ? error.message : String(error));
  }
}

/**
 * Save: a new human revision from the form's edit, with the revision the
 * reviewer opened as its parent. Earlier revisions are never changed.
 */
export async function saveHumanEdit(
  { incidents, videos }: Pick<DbAdapters, 'incidents' | 'videos'>,
  input: { videoId: string; parentId: number; edit: StructuredEdit; createdBy: string; note?: string | null },
  embed: EmbedEvidence,
  reviewerStore?: ReviewerEvidenceStore,
): Promise<RevisionSummary> {
  const parent = await incidents.getRevision(input.videoId, input.parentId);
  if (!parent) throw new StoreRejection('not-found', `Revision not found for this video: ${input.parentId}`);
  const video = await videos.getVideo(input.videoId);
  if (!video) throw new StoreRejection('not-found', `Video not found: ${input.videoId}`);
  checkImages(parent, input.edit, video.storageKey);
  if (!reviewerStore && hasReviewerItems(input.edit))
    throw new StoreRejection('invalid', 'Reviewer items cannot be stored: frame storage is not configured');
  const content = humanRevisionContent(parent, input.edit);
  const { payload } = content;
  let { evidence } = content;
  // Items from the box tool (or re-tracked): the crop, frame and track are stored now, and their entries replace the ones
  // the generic content built (which has no image for them).
  const reviewerEntries =
    reviewerStore && video.storageKey ? await storeReviewerItems(reviewerStore, video.storageKey, input.edit, parent.evidence) : new Map();
  if (reviewerEntries.size) {
    evidence = [...evidence.filter((entry) => !reviewerEntries.has(`${entry.item_type}:${entry.item_id}`)), ...reviewerEntries.values()];
  }
  const missing = missingItemFields(payload);
  if (missing.length)
    throw new StoreRejection(
      'invalid',
      `The saved report would not satisfy incident-contract-v4: ${missing.map((v) => v.message).join('; ')}`,
    );
  const crops = video.officialRevisionId === null ? await searchableCrops(payload, evidence, embed) : [];
  return incidents.saveHumanRevision({
    videoId: input.videoId,
    parentId: input.parentId,
    payload,
    evidence,
    crops,
    createdBy: input.createdBy,
    note: input.note?.trim() || null,
  });
}

// SPDX-License-Identifier: Apache-2.0

// Browser-safe shape of a structured (Class A) reviewer edit and the helpers
// the editor uses. Identifiers are managed here, never typed by the reviewer:
// every change renumbers E#/I#/A# by position and carries instrument holders
// with their entity. Each item keeps the ID it had in the revision the
// reviewer opened (source_id; null for an item the reviewer added), so its
// evidence follows it through a delete or reorder. The edit is the reviewer's
// whole report: saving it appends a new human revision (lib/reports/revision.ts).
// Validation happens server-side (lib/contract/structured-edit.ts) and the
// database function checks the rest.

import type { ReportView } from '@/modules/reports/domain/report-view';
import { firstSeen, type ReviewerDraftItem, type ReviewerPayload, reviewerPayload } from '@/modules/reports/domain/reviewer-item';

export const EDITABLE_INCIDENT_TYPES = ['road accident', 'burglary', 'explosion', 'assault', 'animal attack'] as const;
export const EDITABLE_ENTITY_TYPES = ['human', 'animal', 'unknown'] as const;

/** Fields every item carries: when it is most clearly visible, its detector label and its evidence image (an R2 key). */
interface EditItemDetails {
  source_id: string | null;
  timestamp_seconds: number;
  label: string;
  image: string | null;
  image_timestamp_seconds?: number | null;
  image_box?: number[] | null;
  /** A reviewer-boxed or re-tracked item: the server cuts its crop and frame at the first-seen second and stores its track. */
  reviewer?: ReviewerPayload | null;
}
/** An entity's on-screen span, in seconds from the video start (the contract's first/last seen). */
interface EditEntitySpan {
  first_seen_seconds: number;
  last_seen_seconds: number;
}

export interface StructuredEdit {
  incident: { type: string; start_timestamp: number; end_timestamp: number; description: string; severity_level: number };
  entities: Array<{ entity_id: string; type: string; description: string } & EditEntitySpan & EditItemDetails>;
  instruments: Array<
    { instrument_id: string; entity_id: string | null; name: string; description: string; threat_level: number } & EditItemDetails
  >;
  assets: Array<{ asset_id: string; name: string; description: string } & EditItemDetails>;
}

/**
 * Draft item details: imageUrl is a signed URL for showing the image, never sent, or the object URL of a pending crop.
 * pendingImage is a reviewer-cropped JPEG held in the browser until Save uploads it; its key is then `image`.
 * imageTimestampSeconds and imageBox (the [ymin, xmin, ymax, xmax] box, 0-1000) describe a
 * reviewer-drawn image only; they stay null for an image the opened revision already had.
 */
interface DraftItemDetails {
  key: string;
  sourceId: string | null;
  timestampSeconds: number | null;
  label: string;
  image: string | null;
  imageUrl: string | null;
  pendingImage: Blob | null;
  imageTimestampSeconds: number | null;
  imageBox: number[] | null;
  /** Set by the box tool (or a re-track): Save stores the track and cuts the evidence image from its first-seen second. */
  reviewer: ReviewerDraftItem | null;
}

/** An in-progress edit: values a report may lack (type, times, threat level) start empty and must be chosen. */
export interface EditDraft {
  incident: {
    type: string | null;
    start_timestamp: number | null;
    end_timestamp: number | null;
    description: string;
    severity_level: number | null;
  };
  entities: Array<
    {
      type: string;
      description: string;
      firstSeenSeconds: number | null;
      lastSeenSeconds: number | null;
      canonicalId: string;
    } & DraftItemDetails
  >;
  instruments: Array<{ holderKey: string | null; name: string; description: string; threat_level: number | null } & DraftItemDetails>;
  assets: Array<{ name: string; description: string } & DraftItemDetails>;
}

let keySequence = 0;
export const newKey = (prefix: string) => {
  keySequence += 1;
  return `${prefix}-${Date.now().toString(36)}-${keySequence}`;
};

/** Compute the next sequential canonical entity ID (e.g. 'E1', 'E2', 'E3') that never collides. */
export function nextCanonicalEntityId(items: Array<{ canonicalId?: string; sourceId?: string | null }>): string {
  let max = 0;
  for (const item of items) {
    for (const raw of [item.canonicalId, item.sourceId]) {
      if (!raw) continue;
      const match = /^E(\d+)$/i.exec(raw);
      if (match) {
        const num = parseInt(match[1], 10);
        if (num > max) max = num;
      }
    }
  }
  return `E${max + 1}`;
}

/** Create a new draft entity with a sensible canonical ID that does not collide. */
export function newEntityDraft(draft: EditDraft, overrides?: Partial<EditDraft['entities'][number]>): EditDraft['entities'][number] {
  return {
    ...newItemDetails('entity', draft),
    canonicalId: nextCanonicalEntityId(draft.entities),
    type: 'unknown',
    description: '',
    firstSeenSeconds: draft.incident.start_timestamp,
    lastSeenSeconds: draft.incident.end_timestamp ?? draft.incident.start_timestamp,
    ...overrides,
  };
}

/** A new item's details: visible from the incident start, no label or image yet. */
export function newItemDetails(prefix: string, draft: EditDraft): DraftItemDetails {
  return {
    key: newKey(prefix),
    sourceId: null,
    timestampSeconds: draft.incident.start_timestamp,
    label: '',
    image: null,
    imageUrl: null,
    pendingImage: null,
    imageTimestampSeconds: null,
    imageBox: null,
    reviewer: null,
  };
}

/**
 * Start an edit from the opened revision's structured values. Values outside
 * the contract (e.g. an unknown type, an unrated threat) start empty and must
 * be chosen before saving.
 */
function details(
  key: string,
  sourceId: string,
  item: { timestampSeconds: number | null; label: string; evidence: { image: string | null; imageUrl: string | null } | null },
): DraftItemDetails {
  return {
    key,
    sourceId,
    timestampSeconds: item.timestampSeconds,
    label: item.label,
    image: item.evidence?.image ?? null,
    imageUrl: item.evidence?.imageUrl ?? null,
    pendingImage: null,
    imageTimestampSeconds: null,
    imageBox: null,
    reviewer: null,
  };
}

export function draftFromView(view: ReportView): EditDraft {
  const { incident, entities, instruments, assets } = view.structured;
  const allowedType = (EDITABLE_INCIDENT_TYPES as readonly string[]).includes(incident.type ?? '') ? incident.type : null;
  const entityKeys = new Map(entities.map((entity, index) => [entity.entityId, `entity-${index}`]));
  return {
    incident: {
      type: allowedType,
      start_timestamp: incident.startSeconds,
      end_timestamp: incident.endSeconds,
      description: incident.description ?? '',
      severity_level:
        incident.severityLevel !== null && incident.severityLevel >= 1 && incident.severityLevel <= 5 ? incident.severityLevel : null,
    },
    entities: entities.map((entity, index) => ({
      ...details(`entity-${index}`, entity.entityId, entity),
      canonicalId: entity.entityId || `E${index + 1}`,
      type: (EDITABLE_ENTITY_TYPES as readonly string[]).includes(entity.type) ? entity.type : 'unknown',
      description: entity.description,
      // A revision saved before contract v4 has no span: start from the one second it is known to be visible.
      firstSeenSeconds: entity.firstSeenSeconds ?? entity.timestampSeconds,
      lastSeenSeconds: entity.lastSeenSeconds ?? entity.timestampSeconds,
    })),
    instruments: instruments.map((instrument, index) => ({
      ...details(`instrument-${index}`, instrument.instrumentId, instrument),
      holderKey: instrument.entityId ? (entityKeys.get(instrument.entityId) ?? null) : null,
      name: instrument.name,
      description: instrument.description,
      threat_level:
        instrument.threatLevel !== null && instrument.threatLevel >= 1 && instrument.threatLevel <= 5 ? instrument.threatLevel : null,
    })),
    assets: assets.map((asset, index) => ({
      ...details(`asset-${index}`, asset.assetId, asset),
      name: asset.name,
      description: asset.description,
    })),
  };
}

/** The crops in the draft that Save must upload first, by item key. */
export function pendingImages(draft: EditDraft): Array<{ itemId: string; blob: Blob }> {
  return [...draft.entities, ...draft.instruments, ...draft.assets].flatMap((item) =>
    item.pendingImage ? [{ itemId: item.key, blob: item.pendingImage }] : [],
  );
}

/** The draft with each uploaded crop's R2 key in place of its pending JPEG: the edit Save sends. */
export function withUploadedImages(draft: EditDraft, keys: ReadonlyMap<string, string>): EditDraft {
  const place = <T extends DraftItemDetails>(item: T): T => {
    const key = keys.get(item.key);
    return key === undefined ? item : { ...item, image: key, pendingImage: null };
  };
  return { ...draft, entities: draft.entities.map(place), instruments: draft.instruments.map(place), assets: draft.assets.map(place) };
}

/** Problems that stop the draft from becoming a complete edit (shown before sending). */
export function draftProblems(draft: EditDraft): string[] {
  const problems: string[] = [];
  const { incident } = draft;
  if (!incident.type) problems.push('Choose an incident type.');
  if (incident.start_timestamp === null || incident.end_timestamp === null) problems.push('Enter the incident start and end in seconds.');
  else if (incident.end_timestamp < incident.start_timestamp) problems.push('The incident end must not be before its start.');
  if (incident.severity_level === null) problems.push('Choose a severity level.');
  if (draft.instruments.some((instrument) => instrument.threat_level === null))
    problems.push('Rate the threat level of every instrument (1-5).');
  if ([...draft.entities, ...draft.instruments, ...draft.assets].some((item) => item.timestampSeconds === null))
    problems.push('Enter the second each entity, instrument and asset is most clearly visible.');
  for (const entity of draft.entities) {
    const { firstSeenSeconds: first, lastSeenSeconds: last, timestampSeconds: seen } = entity;
    if (first === null || last === null) problems.push('Enter the first and last second each entity is seen.');
    else if (last < first) problems.push("An entity's last seen must not be before its first seen.");
    else if (seen !== null && (seen < first || seen > last))
      problems.push('The second an entity is most clearly visible must lie within its first to last seen.');
  }
  return [...new Set(problems)];
}

const sent = (item: DraftItemDetails): EditItemDetails => ({
  source_id: item.sourceId,
  timestamp_seconds: item.timestampSeconds ?? 0,
  label: item.label,
  image: item.image,
  ...(item.imageTimestampSeconds !== null || item.imageBox
    ? { image_timestamp_seconds: item.imageTimestampSeconds, image_box: item.imageBox }
    : {}),
  ...(item.reviewer ? { reviewer: reviewerPayload(item.reviewer) } : {}),
});

/** The edit sent to the server: sequential IDs by position, holders resolved to the renumbered entity IDs, each item's source_id kept. */
export function editFromDraft(draft: EditDraft): StructuredEdit {
  const problems = draftProblems(draft);
  if (problems.length) throw new Error(problems.join(' '));
  const entityIdByKey = new Map(draft.entities.map((entity, index) => [entity.key, `E${index + 1}`]));
  return {
    incident: {
      type: draft.incident.type ?? '',
      start_timestamp: draft.incident.start_timestamp ?? 0,
      end_timestamp: draft.incident.end_timestamp ?? 0,
      description: draft.incident.description,
      severity_level: draft.incident.severity_level ?? 0,
    },
    entities: draft.entities.map((entity, index) => ({
      entity_id: `E${index + 1}`,
      type: entity.type,
      description: entity.description,
      first_seen_seconds: entity.firstSeenSeconds ?? 0,
      last_seen_seconds: entity.lastSeenSeconds ?? 0,
      ...sent(entity),
    })),
    instruments: draft.instruments.map((instrument, index) => ({
      instrument_id: `I${index + 1}`,
      entity_id: instrument.holderKey ? (entityIdByKey.get(instrument.holderKey) ?? null) : null,
      name: instrument.name,
      description: instrument.description,
      threat_level: instrument.threat_level ?? 0,
      ...sent(instrument),
    })),
    assets: draft.assets.map((asset, index) => ({
      asset_id: `A${index + 1}`,
      name: asset.name,
      description: asset.description,
      ...sent(asset),
    })),
  };
}

/**
 * Adds an item from the box tool to the draft as a new entity, instrument or asset. The evidence image is the first second
 * the item is seen, so the item is visible from there; an entity's span is its track's. An instrument's holder is the
 * entity whose label matches `holder`, else nobody. The name is the item's label.
 */
export function addReviewerItem(draft: EditDraft, item: ReviewerDraftItem): EditDraft {
  const seen = firstSeen(item);
  const seconds = Math.round(seen.seconds);
  const common = {
    ...newItemDetails(item.itemType, draft),
    timestampSeconds: seconds,
    label: item.name,
    reviewer: item,
  };
  if (item.itemType === 'entity') {
    const span = item.track?.span ?? [seen.seconds, seen.seconds];
    return {
      ...draft,
      entities: [
        ...draft.entities,
        {
          ...common,
          canonicalId: nextCanonicalEntityId(draft.entities),
          type: 'unknown',
          description: item.description,
          firstSeenSeconds: Math.floor(Math.min(span[0], seconds)),
          lastSeenSeconds: Math.ceil(Math.max(span[1], seconds)),
        },
      ],
    };
  }
  if (item.itemType === 'instrument') {
    const holder = item.holder?.trim().toLowerCase();
    const holderKey = holder ? (draft.entities.find((entity) => entity.label.trim().toLowerCase() === holder)?.key ?? null) : null;
    return {
      ...draft,
      instruments: [
        ...draft.instruments,
        { ...common, holderKey, name: item.name, description: item.description, threat_level: item.threatLevel ?? null },
      ],
    };
  }
  return { ...draft, assets: [...draft.assets, { ...common, name: item.name, description: item.description }] };
}

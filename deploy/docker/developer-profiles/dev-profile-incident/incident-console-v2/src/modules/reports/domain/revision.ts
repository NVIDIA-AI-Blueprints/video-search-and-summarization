// SPDX-License-Identifier: Apache-2.0

// Browser-safe shapes of a report revision (report_revisions) and the pure
// rules for building a human revision from the one the reviewer opened.
//
// A revision is a full snapshot: payload is the whole incident-contract-v3
// report and evidence lists the R2 image keys and crop details its items use.
// Revisions are append-only. Save writes a new human revision whose parent is
// the revision the reviewer opened; nothing is ever updated.

import type { StructuredEdit } from '@/modules/reports/domain/edit-draft';

export type RevisionKind = 'ai' | 'human';
export type EvidenceItemType = 'entity' | 'instrument' | 'asset';

/**
 * One entry of report_revisions.evidence. Keys this console does not know are
 * kept as they are when a later revision copies the entry.
 */
export interface EvidenceEntry {
  item_type: EvidenceItemType;
  item_id: string;
  /** R2 key of the crop (what evidence search embeds) or uploaded image; null when the item has none (e.g. a gateway-mode run, or an object not located). */
  image: string | null;
  /** [ymin, xmin, ymax, xmax] normalised to 0-1000 in the video frame; null for an uploaded image. */
  bounding_box: number[] | null;
  /** The video second the crop was taken from; null for an uploaded image. */
  timestamp_seconds: number | null;
  // An AI run also stores the whole video frame (frame: R2 key), the second the box was found at (box_seconds), and
  // located: false for an object it did not find (no crop, no box). These ride along in the index signature.
  [key: string]: unknown;
}

/** One row of an incident's History list (newest first). */
export interface RevisionSummary {
  revisionId: number;
  videoId: string;
  revisionNo: number;
  kind: RevisionKind;
  /** The reviewer for a human revision; null for an AI revision. */
  createdBy: string | null;
  /** Naive UTC, as stored. */
  createdAt: string | null;
  note: string | null;
  parentId: number | null;
  /** The run whose model output the revision derives from (a human revision inherits its parent's). */
  modelRunId: string | null;
  /** The run's model, when the run is recorded. */
  model: string | null;
  isOfficial: boolean;
}

/** A report payload as stored: the contract report, read leniently. */
export type ReportPayload = Record<string, unknown>;

const ITEM_ID: Record<EvidenceItemType, string> = { entity: 'entity_id', instrument: 'instrument_id', asset: 'asset_id' };
const COLLECTION: Record<EvidenceItemType, 'entities' | 'instruments' | 'assets'> = {
  entity: 'entities',
  instrument: 'instruments',
  asset: 'assets',
};
const ITEM_TYPES: EvidenceItemType[] = ['entity', 'instrument', 'asset'];

const asRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
const integerOrNull = (value: unknown): number | null => (typeof value === 'number' && Number.isInteger(value) ? value : null);

/** report_revisions.evidence -> its well-formed entries; anything else is dropped. */
export function evidenceEntries(value: unknown): EvidenceEntry[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((raw): EvidenceEntry[] => {
    const entry = asRecord(raw);
    const type = entry.item_type;
    if (typeof type !== 'string' || !ITEM_TYPES.includes(type as EvidenceItemType) || typeof entry.item_id !== 'string' || !entry.item_id)
      return [];
    const box =
      Array.isArray(entry.bounding_box) && entry.bounding_box.length === 4 && entry.bounding_box.every((n) => typeof n === 'number')
        ? (entry.bounding_box as number[])
        : null;
    return [
      {
        ...entry,
        item_type: type as EvidenceItemType,
        item_id: entry.item_id,
        image: typeof entry.image === 'string' && entry.image.trim() ? entry.image : null,
        bounding_box: box,
        timestamp_seconds: integerOrNull(entry.timestamp_seconds),
      },
    ];
  });
}

/** The evidence placeholder of a run that cropped nothing: one image-less entry per item. */
export function placeholderEvidence(payload: ReportPayload, reason: string): EvidenceEntry[] {
  return ITEM_TYPES.flatMap((type) =>
    (Array.isArray(payload[COLLECTION[type]]) ? (payload[COLLECTION[type]] as unknown[]) : [])
      .map(asRecord)
      .filter((item) => typeof item[ITEM_ID[type]] === 'string')
      .map((item) => ({
        item_type: type,
        item_id: String(item[ITEM_ID[type]]),
        image: null,
        bounding_box: null,
        timestamp_seconds: integerOrNull(item.timestamp_seconds),
        missing: reason,
      })),
  );
}

/** The R2 folder of every evidence image of a video: `evidence/<video key>/`, so it reads like the video. */
export function evidenceFolder(videoKey: string): string {
  return `evidence/${videoKey}/`;
}

/**
 * The R2 key of a reviewer's evidence image: `evidence/<video key>/edit-<n>/<itemId>.jpg`.
 * The caller picks the first `n` whose key is free, so no object is ever overwritten.
 */
export function uploadedEvidenceKey(videoKey: string, editNumber: number, itemId: string): string {
  return `${evidenceFolder(videoKey)}edit-${editNumber}/${itemId}.jpg`;
}

/** Whether an image key may appear in a revision of the video stored under `videoKey`. */
export function isEvidenceKeyOfVideo(key: string, videoKey: string): boolean {
  return key.startsWith(evidenceFolder(videoKey)) && !key.includes('..') && key.length <= 1024;
}

/**
 * The human revision saved from an edit of `parent`: the parent's payload
 * with the reviewer's incident fields and items, and evidence that follows
 * each item from the parent item it came from (source_id). An item whose
 * image the reviewer replaced gets an upload entry instead, carrying the
 * second and box the reviewer drew it from when the edit has them; an item without
 * an image gets none. Model-only fields (title, severity rationale,
 * confidence, location, timeline, uncertainties) are kept from the parent.
 */
export function humanRevisionContent(
  parent: { payload: ReportPayload; evidence: EvidenceEntry[] },
  edit: StructuredEdit,
): { payload: ReportPayload; evidence: EvidenceEntry[] } {
  const incident = asRecord(parent.payload.incident);
  const payload: ReportPayload = {
    ...parent.payload,
    incident: {
      ...incident,
      ...edit.incident,
      duration: edit.incident.end_timestamp - edit.incident.start_timestamp,
    },
    entities: edit.entities.map(({ source_id: _source, image: _image, image_timestamp_seconds: _at, image_box: _box, ...item }) => item),
    instruments: edit.instruments.map(
      ({ source_id: _source, image: _image, image_timestamp_seconds: _at, image_box: _box, ...item }) => item,
    ),
    assets: edit.assets.map(({ source_id: _source, image: _image, image_timestamp_seconds: _at, image_box: _box, ...item }) => item),
  };
  const evidence: EvidenceEntry[] = [];
  const follow = (
    type: EvidenceItemType,
    itemId: string,
    sourceId: string | null,
    image: string | null,
    crop: { box?: number[] | null; seconds?: number | null },
  ) => {
    const base = sourceId ? parent.evidence.find((entry) => entry.item_type === type && entry.item_id === sourceId) : undefined;
    // An object the detector did not find has a frame but no crop: it follows its item while the reviewer leaves it be.
    if (!image) {
      if (base && !base.image && typeof base.frame === 'string') evidence.push({ ...base, item_id: itemId });
      return;
    }
    evidence.push(
      base && base.image === image
        ? { ...base, item_id: itemId }
        : {
            item_type: type,
            item_id: itemId,
            image,
            bounding_box: crop.box ?? null,
            timestamp_seconds: crop.seconds ?? null,
            source: 'upload',
          },
    );
  };
  for (const item of edit.entities) {
    follow('entity', item.entity_id, item.source_id, item.image, { box: item.image_box, seconds: item.image_timestamp_seconds });
  }
  for (const item of edit.instruments) {
    follow('instrument', item.instrument_id, item.source_id, item.image, { box: item.image_box, seconds: item.image_timestamp_seconds });
  }
  for (const item of edit.assets) {
    follow('asset', item.asset_id, item.source_id, item.image, { box: item.image_box, seconds: item.image_timestamp_seconds });
  }
  return { payload, evidence };
}

/** The facet columns of a revision, read from its payload (written by the caller, not generated). */
export function revisionFacets(payload: ReportPayload): {
  incident_type: string | null;
  severity_level: number | null;
  start_seconds: number | null;
  end_seconds: number | null;
} {
  const incident = asRecord(payload.incident);
  return {
    incident_type: typeof incident.type === 'string' && incident.type.trim() ? incident.type.trim().toLowerCase() : null,
    severity_level: integerOrNull(incident.severity_level),
    start_seconds: integerOrNull(incident.start_timestamp),
    end_seconds: integerOrNull(incident.end_timestamp),
  };
}

/** One evidence vector the agent returns for a stored revision. */
export interface EvidenceEmbedding {
  itemType: EvidenceItemType;
  itemId: string;
  embedding: number[];
}

/** One row of set_official_revision's p_crops: everything evidence_crops stores for an official revision. */
export interface OfficialCrop {
  item_type: EvidenceItemType;
  item_id: string;
  image: string;
  bounding_box: number[] | null;
  timestamp_seconds: number | null;
  item_name: string;
  description: string;
  threat_level: number | null;
  holder_entity_id: string | null;
  embedding: number[];
}

/** The item details the payload holds, used when an evidence entry (e.g. a gateway-mode placeholder) lacks them. */
function payloadDetails(
  payload: ReportPayload,
): Map<string, { item_name: string; description: string; threat_level: number | null; holder_entity_id: string | null }> {
  const details = new Map<
    string,
    { item_name: string; description: string; threat_level: number | null; holder_entity_id: string | null }
  >();
  const add = (
    type: EvidenceItemType,
    id: unknown,
    name: unknown,
    description: unknown,
    threat: unknown,
    holder: unknown,
    fallback: string,
  ) => {
    if (typeof id !== 'string' || !id) return;
    details.set(`${type}:${id}`, {
      item_name: typeof name === 'string' && name.trim() ? name : fallback,
      description: typeof description === 'string' ? description : '',
      threat_level: integerOrNull(threat),
      holder_entity_id: typeof holder === 'string' && holder ? holder : null,
    });
  };
  for (const item of Array.isArray(payload.entities) ? payload.entities : []) {
    const entity = asRecord(item);
    add('entity', entity.entity_id, entity.type, entity.description, null, null, 'entity');
  }
  for (const item of Array.isArray(payload.instruments) ? payload.instruments : []) {
    const instrument = asRecord(item);
    add(
      'instrument',
      instrument.instrument_id,
      instrument.name,
      instrument.description,
      instrument.threat_level,
      instrument.entity_id,
      'instrument',
    );
  }
  for (const item of Array.isArray(payload.assets) ? payload.assets : []) {
    const asset = asRecord(item);
    add('asset', asset.asset_id, asset.name, asset.description, null, null, 'asset');
  }
  return details;
}

/**
 * The p_crops of an official revision: one row per evidence item that has an
 * image and an embedding, with the item details the agent stored on the entry
 * (or, for a gateway-mode placeholder, from the payload). An item with no
 * image or no vector is not searchable and is left out. The row's second is the
 * one the box was found at (box_seconds, a retry second when the item's own
 * second found nothing), so a search hit cuts the frame the box belongs to.
 */
export function officialCrops(payload: ReportPayload, evidence: EvidenceEntry[], embeddings: EvidenceEmbedding[]): OfficialCrop[] {
  const details = payloadDetails(payload);
  const vectors = new Map(embeddings.map((item) => [`${item.itemType}:${item.itemId}`, item.embedding]));
  return evidence.flatMap((entry): OfficialCrop[] => {
    if (!entry.image) return [];
    const embedding = vectors.get(`${entry.item_type}:${entry.item_id}`);
    if (!embedding) return [];
    const fallback = details.get(`${entry.item_type}:${entry.item_id}`);
    return [
      {
        item_type: entry.item_type,
        item_id: entry.item_id,
        image: entry.image,
        bounding_box: entry.bounding_box,
        timestamp_seconds: integerOrNull(entry.box_seconds) ?? entry.timestamp_seconds ?? 0,
        item_name:
          typeof entry.item_name === 'string' && entry.item_name.trim() ? entry.item_name : (fallback?.item_name ?? entry.item_type),
        description: typeof entry.description === 'string' ? entry.description : (fallback?.description ?? ''),
        threat_level: integerOrNull(entry.threat_level) ?? fallback?.threat_level ?? null,
        holder_entity_id:
          typeof entry.holder_entity_id === 'string' && entry.holder_entity_id
            ? entry.holder_entity_id
            : (fallback?.holder_entity_id ?? null),
        embedding,
      },
    ];
  });
}

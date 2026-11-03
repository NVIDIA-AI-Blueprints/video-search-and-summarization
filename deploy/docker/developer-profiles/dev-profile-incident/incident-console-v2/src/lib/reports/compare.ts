// SPDX-License-Identifier: Apache-2.0

// Edit mode's side-by-side rows: the version the draft came from (A) beside
// the draft (B). Items keep the stored ID they came from (sourceId), so an
// item that was edited or moved stays in the same row; an item only in A is
// shown as deleted, and an item only in the draft as new. The same module
// carries the draft operations the rows need: delete (an entity with the
// instruments it holds), revert to A, and the change count the bottom bar
// shows. Everything here is pure and browser-safe.

import { EDITABLE_ENTITY_TYPES, EDITABLE_INCIDENT_TYPES, newKey, type EditDraft } from '@/modules/reports/domain/edit-draft';
import type {
  ReportView,
  StructuredAsset,
  StructuredEntity,
  StructuredIncident,
  StructuredInstrument,
} from '@/modules/reports/domain/report-view';

export type PairKind = 'entity' | 'instrument' | 'asset';
export type Structured = ReportView['structured'];
export type SavedItem = StructuredEntity | StructuredInstrument | StructuredAsset;
type DraftEntity = EditDraft['entities'][number];
type DraftInstrument = EditDraft['instruments'][number];
type DraftAsset = EditDraft['assets'][number];
export type DraftItem = DraftEntity | DraftInstrument | DraftAsset;

export interface PairRow {
  kind: PairKind;
  /** The item as it is in A; null when the draft added it. */
  a: SavedItem | null;
  /** The item as it is in the draft; null when it was deleted. */
  b: DraftItem | null;
}

/** One entity with the instruments it holds, then the loose instruments and the assets. */
export interface PairUnit {
  kind: 'entity' | 'loose' | 'assets';
  entity: PairRow | null;
  rows: PairRow[];
}

export interface Pairing {
  units: PairUnit[];
  rows: PairRow[];
  /** Items in the draft, the number the evidence bar shows. */
  liveCount: number;
}

export const isSavedEntity = (item: SavedItem): item is StructuredEntity => 'type' in item;
export const isSavedInstrument = (item: SavedItem): item is StructuredInstrument => 'threatLevel' in item;
export const isSavedAsset = (item: SavedItem): item is StructuredAsset => 'name' in item && !('threatLevel' in item) && !('type' in item);

export const isDraftEntity = (item: DraftItem): item is DraftEntity => 'type' in item;
export const isDraftInstrument = (item: DraftItem): item is DraftInstrument => 'threat_level' in item;

/** The stored ID an A item is known by. */
export function savedItemId(item: SavedItem): string {
  if (isSavedEntity(item)) return item.entityId;
  if (isSavedInstrument(item)) return item.instrumentId;
  return item.assetId;
}

/** The text a card shows as the item's name. */
export function savedItemName(item: SavedItem): string {
  if (isSavedEntity(item)) return item.label || item.type;
  return item.name;
}

export function draftItemName(item: DraftItem): string {
  if (isDraftEntity(item)) return item.label || item.type;
  return item.name;
}

const savedTimestamp = (item: SavedItem | null): number => item?.timestampSeconds ?? Number.POSITIVE_INFINITY;
const draftTimestamp = (item: DraftItem | null): number => item?.timestampSeconds ?? Number.POSITIVE_INFINITY;
const pairTimestamp = (row: PairRow) => Math.min(draftTimestamp(row.b), savedTimestamp(row.a));

function canonicalEntityRank(row: PairRow): [number, string] {
  const id =
    (row.b && 'canonicalId' in row.b && typeof row.b.canonicalId === 'string' ? row.b.canonicalId : null) ??
    (row.a && isSavedEntity(row.a) ? row.a.entityId : null) ??
    row.b?.sourceId ??
    '';
  const match = /^E(\d+)$/i.exec(id);
  return match ? [parseInt(match[1], 10), id] : [Number.POSITIVE_INFINITY, id];
}

function pairCollection(kind: PairKind, saved: SavedItem[], draft: DraftItem[]): PairRow[] {
  const paired: PairRow[] = draft.map((item) => ({
    kind,
    a: item.sourceId ? (saved.find((candidate) => savedItemId(candidate) === item.sourceId) ?? null) : null,
    b: item,
  }));
  const onlyA: PairRow[] = saved
    .filter((candidate) => !draft.some((item) => item.sourceId === savedItemId(candidate)))
    .map((item) => ({ kind, a: item, b: null }));

  if (kind === 'entity') {
    return [...paired, ...onlyA].sort((left, right) => {
      const [leftNum, leftId] = canonicalEntityRank(left);
      const [rightNum, rightId] = canonicalEntityRank(right);
      if (leftNum !== rightNum) return leftNum - rightNum;
      return leftId.localeCompare(rightId, undefined, { numeric: true });
    });
  }

  return [...paired, ...onlyA].sort((left, right) => pairTimestamp(left) - pairTimestamp(right));
}

/** A and the draft as paired rows, grouped the way the evidence section shows them. */
export function pairReport(a: Structured | null, draft: EditDraft): Pairing {
  const entities = pairCollection('entity', a?.entities ?? [], draft.entities);
  const instruments = pairCollection('instrument', a?.instruments ?? [], draft.instruments);
  const assets = pairCollection('asset', a?.assets ?? [], draft.assets);

  const units: PairUnit[] = entities.map((entity) => ({ kind: 'entity', entity, rows: [] }));
  const loose: PairRow[] = [];
  for (const row of instruments) {
    const holderKey = row.b && isDraftInstrument(row.b) ? row.b.holderKey : null;
    const holderId = row.a && isSavedInstrument(row.a) ? row.a.entityId : null;
    const unit = units.find((candidate) => {
      if (holderKey) return candidate.entity?.b?.key === holderKey;
      const entityA = candidate.entity?.a ?? null;
      return holderId !== null && entityA !== null && isSavedEntity(entityA) && entityA.entityId === holderId;
    });
    (unit ? unit.rows : loose).push(row);
  }

  if (loose.length) units.push({ kind: 'loose', entity: null, rows: loose });
  if (assets.length) units.push({ kind: 'assets', entity: null, rows: assets });
  const rows = [...entities, ...instruments, ...assets];
  return { units, rows, liveCount: rows.filter((row) => row.b).length };
}

/** The draft item a stored entity ID became, if the draft still has it. */
function draftEntityFor(draft: EditDraft, savedEntityId: string | null): DraftEntity | undefined {
  return savedEntityId ? draft.entities.find((entity) => entity.sourceId === savedEntityId) : undefined;
}

/** Whether B differs from A in a way the reviewer can see; a deleted item differs too. */
export function rowDiffers(row: PairRow, draft: EditDraft): boolean {
  if (!row.a || !row.b) return true;
  const { a, b } = row;
  if ((a.evidence?.image ?? null) !== (b.image ?? null)) return true;
  if (a.timestampSeconds !== b.timestampSeconds || a.label !== b.label) return true;
  if (isSavedEntity(a) && isDraftEntity(b)) return a.type !== b.type || a.description !== b.description;
  if (isSavedInstrument(a) && isDraftInstrument(b)) {
    const holder = draftEntityFor(draft, a.entityId);
    return (
      a.name !== b.name || a.description !== b.description || a.threatLevel !== b.threat_level || (holder?.key ?? null) !== b.holderKey
    );
  }
  if (isSavedAsset(a) && !isDraftEntity(b) && !isDraftInstrument(b)) return a.name !== b.name || a.description !== b.description;
  return false;
}

/** Incident fields and items that differ from the revision the draft started from. */
export function draftChangeCount(base: Structured, draft: EditDraft): number {
  const { incident } = base;
  const fields: Array<[unknown, unknown]> = [
    [incident.type, draft.incident.type],
    [incident.startSeconds, draft.incident.start_timestamp],
    [incident.endSeconds, draft.incident.end_timestamp],
    [incident.description ?? '', draft.incident.description],
    [incident.severityLevel, draft.incident.severity_level],
  ];
  return (
    pairReport(base, draft).rows.filter((row) => rowDiffers(row, draft)).length + fields.filter(([left, right]) => left !== right).length
  );
}

/** An instrument can only go back to A while the entity that holds it in A is in the draft. */
export function revertBlocked(row: PairRow, draft: EditDraft): boolean {
  return !!row.a && isSavedInstrument(row.a) && !!row.a.entityId && !draftEntityFor(draft, row.a.entityId);
}

/** Delete a draft item; deleting an entity also deletes the instruments it holds. */
export function deleteDraftItem(draft: EditDraft, kind: PairKind, key: string): EditDraft {
  if (kind === 'entity') {
    return {
      ...draft,
      entities: draft.entities.filter((entity) => entity.key !== key),
      instruments: draft.instruments.filter((instrument) => instrument.holderKey !== key),
    };
  }
  if (kind === 'instrument') return { ...draft, instruments: draft.instruments.filter((instrument) => instrument.key !== key) };
  return { ...draft, assets: draft.assets.filter((asset) => asset.key !== key) };
}

const editableEntityType = (type: string): string => ((EDITABLE_ENTITY_TYPES as readonly string[]).includes(type) ? type : 'unknown');
const editableIncidentType = (type: string | null): string | null =>
  type && (EDITABLE_INCIDENT_TYPES as readonly string[]).includes(type) ? type : null;
const editableThreat = (value: number | null): number | null => (value !== null && value >= 1 && value <= 5 ? value : null);

function entityFromSaved(item: StructuredEntity, key: string): DraftEntity {
  return {
    key,
    canonicalId: item.entityId,
    sourceId: item.entityId,
    timestampSeconds: item.timestampSeconds,
    label: item.label,
    image: item.evidence?.image ?? null,
    imageUrl: item.evidence?.imageUrl ?? null,
    pendingImage: null,
    imageTimestampSeconds: null,
    imageBox: null,
    reviewer: null,
    type: editableEntityType(item.type),
    description: item.description,
    firstSeenSeconds: item.firstSeenSeconds ?? item.timestampSeconds,
    lastSeenSeconds: item.lastSeenSeconds ?? item.timestampSeconds,
  };
}

function instrumentFromSaved(item: StructuredInstrument, key: string, holderKey: string | null): DraftInstrument {
  return {
    key,
    sourceId: item.instrumentId,
    timestampSeconds: item.timestampSeconds,
    label: item.label,
    image: item.evidence?.image ?? null,
    imageUrl: item.evidence?.imageUrl ?? null,
    pendingImage: null,
    imageTimestampSeconds: null,
    imageBox: null,
    reviewer: null,
    holderKey,
    name: item.name,
    description: item.description,
    threat_level: editableThreat(item.threatLevel),
  };
}

function assetFromSaved(item: StructuredAsset, key: string): DraftAsset {
  return {
    key,
    sourceId: item.assetId,
    timestampSeconds: item.timestampSeconds,
    label: item.label,
    image: item.evidence?.image ?? null,
    imageUrl: item.evidence?.imageUrl ?? null,
    pendingImage: null,
    imageTimestampSeconds: null,
    imageBox: null,
    reviewer: null,
    name: item.name,
    description: item.description,
  };
}

function restoreEntity(draft: EditDraft, a: Structured, entity: StructuredEntity): EditDraft {
  const existing = draft.entities.find((candidate) => candidate.sourceId === entity.entityId);
  const restored = entityFromSaved(entity, existing?.key ?? newKey('entity'));
  const entities = existing
    ? draft.entities.map((candidate) => (candidate.key === existing.key ? restored : candidate))
    : [...draft.entities, restored];
  const instruments = [...draft.instruments];
  for (const instrument of a.instruments.filter((candidate) => candidate.entityId === entity.entityId)) {
    if (instruments.some((candidate) => candidate.sourceId === instrument.instrumentId)) continue;
    instruments.push(instrumentFromSaved(instrument, newKey('instrument'), restored.key));
  }
  return { ...draft, entities, instruments };
}

/** Put an item back exactly as A has it; reverting an entity also brings back the instruments it held. */
export function revertDraftItem(draft: EditDraft, a: Structured, row: PairRow): EditDraft {
  if (!row.a) return draft;
  if (isSavedEntity(row.a)) return restoreEntity(draft, a, row.a);
  if (isSavedInstrument(row.a)) {
    const instrument = row.a;
    const existing = draft.instruments.find((candidate) => candidate.sourceId === instrument.instrumentId);
    const restored = instrumentFromSaved(
      instrument,
      existing?.key ?? newKey('instrument'),
      draftEntityFor(draft, instrument.entityId)?.key ?? null,
    );
    const instruments = existing
      ? draft.instruments.map((candidate) => (candidate.key === existing.key ? restored : candidate))
      : [...draft.instruments, restored];
    return { ...draft, instruments };
  }
  const existing = draft.assets.find((candidate) => candidate.sourceId === (row.a as StructuredAsset).assetId);
  const restored = assetFromSaved(row.a, existing?.key ?? newKey('asset'));
  const assets = existing
    ? draft.assets.map((candidate) => (candidate.key === existing.key ? restored : candidate))
    : [...draft.assets, restored];
  return { ...draft, assets };
}

/** The draft as the structured shape the timeline and the cards read. */
export function draftToStructured(draft: EditDraft): Structured {
  const incident: StructuredIncident = {
    type: editableIncidentType(draft.incident.type),
    startSeconds: draft.incident.start_timestamp,
    endSeconds: draft.incident.end_timestamp,
    duration:
      draft.incident.start_timestamp !== null && draft.incident.end_timestamp !== null
        ? draft.incident.end_timestamp - draft.incident.start_timestamp
        : null,
    description: draft.incident.description,
    severityLevel: draft.incident.severity_level,
    confidenceScore: null,
  };
  const evidence = (image: string | null, imageUrl: string | null, seconds: number | null) => ({
    image,
    imageUrl,
    frame: null,
    frameUrl: null,
    located: null,
    boxSeconds: null,
    imageGap: null,
    boundingBox: null,
    timestampSeconds: seconds,
    uploaded: false,
    track: null,
    trackUrl: null,
    trackFirstSeenSeconds: null,
    trackLastSeenSeconds: null,
    trackSource: null,
    trackPartial: false,
    trackGap: null,
    addedByReviewer: false,
  });
  const entityIdByKey = new Map(draft.entities.map((entity) => [entity.key, entity.canonicalId]));
  return {
    incident,
    entities: draft.entities.map((entity) => ({
      entityId: entity.canonicalId,
      type: entity.type,
      description: entity.description,
      timestampSeconds: entity.timestampSeconds,
      firstSeenSeconds: entity.firstSeenSeconds,
      lastSeenSeconds: entity.lastSeenSeconds,
      label: entity.label,
      evidence: evidence(entity.image, entity.imageUrl, entity.timestampSeconds),
    })),
    instruments: draft.instruments.map((instrument) => ({
      instrumentId: instrument.key,
      entityId: instrument.holderKey ? (entityIdByKey.get(instrument.holderKey) ?? instrument.holderKey) : null,
      name: instrument.name,
      description: instrument.description,
      threatLevel: instrument.threat_level,
      timestampSeconds: instrument.timestampSeconds,
      label: instrument.label,
      evidence: evidence(instrument.image, instrument.imageUrl, instrument.timestampSeconds),
    })),
    assets: draft.assets.map((asset) => ({
      assetId: asset.key,
      name: asset.name,
      description: asset.description,
      timestampSeconds: asset.timestampSeconds,
      label: asset.label,
      evidence: evidence(asset.image, asset.imageUrl, asset.timestampSeconds),
    })),
  };
}

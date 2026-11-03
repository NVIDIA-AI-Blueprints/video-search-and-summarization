// SPDX-License-Identifier: Apache-2.0

// One evidence item of a report version, flattened for the report page's detail pop-up, and the lines the pop-up shows
// for it. Pure functions, no I/O, browser-safe.

import { formatClock } from '@/modules/reports/domain/normalize';
import type { ItemEvidence, StructuredAsset, StructuredEntity, StructuredInstrument } from '@/modules/reports/domain/report-view';
import type { EvidenceItemType } from '@/modules/reports/domain/revision';

export interface EvidenceDetailItem {
  kind: EvidenceItemType;
  /** The canonical id (E1, I1, A1). */
  id: string;
  title: string;
  description: string;
  /** When the item is most clearly visible (its own time, else its evidence's); null when not recorded. */
  seconds: number | null;
  evidence: ItemEvidence | null;
  /** Entities: the names of the instruments they hold. */
  holds: string[];
  /** Entities: the span on screen; null when not recorded. */
  onScreen: { firstSeenSeconds: number; lastSeenSeconds: number } | null;
  /** Instruments: the threat rating; null when unrated. */
  threatLevel: number | null;
  /** Instruments: the holder's "E1 · label" title; null when nobody holds it. */
  holder: string | null;
}

const itemSeconds = (own: number | null, evidence: ItemEvidence | null): number | null => own ?? evidence?.timestampSeconds ?? null;

/** The "E1 · label" title an instrument's holder is named by. */
export const entityTitle = (entity: StructuredEntity) => `${entity.entityId} · ${entity.label || entity.type}`;

export function entityDetail(entity: StructuredEntity, instruments: StructuredInstrument[]): EvidenceDetailItem {
  return {
    kind: 'entity',
    id: entity.entityId,
    title: entity.label || entity.type,
    description: entity.description,
    seconds: itemSeconds(entity.timestampSeconds, entity.evidence),
    evidence: entity.evidence,
    holds: instruments.filter((instrument) => instrument.entityId === entity.entityId).map((instrument) => instrument.name),
    onScreen:
      entity.firstSeenSeconds !== null && entity.lastSeenSeconds !== null
        ? { firstSeenSeconds: entity.firstSeenSeconds, lastSeenSeconds: entity.lastSeenSeconds }
        : null,
    threatLevel: null,
    holder: null,
  };
}

export function instrumentDetail(instrument: StructuredInstrument, entities: StructuredEntity[]): EvidenceDetailItem {
  const holder = entities.find((entity) => entity.entityId === instrument.entityId);
  return {
    kind: 'instrument',
    id: instrument.instrumentId,
    title: instrument.name,
    description: instrument.description,
    seconds: itemSeconds(instrument.timestampSeconds, instrument.evidence),
    evidence: instrument.evidence,
    holds: [],
    onScreen: null,
    threatLevel: instrument.threatLevel,
    holder: holder ? entityTitle(holder) : null,
  };
}

export function assetDetail(asset: StructuredAsset): EvidenceDetailItem {
  return {
    kind: 'asset',
    id: asset.assetId,
    title: asset.name,
    description: asset.description,
    seconds: itemSeconds(asset.timestampSeconds, asset.evidence),
    evidence: asset.evidence,
    holds: [],
    onScreen: null,
    threatLevel: null,
    holder: null,
  };
}

/** What the pop-up prints for an item; a null line is not shown. */
export interface EvidenceDetailLines {
  description: string;
  /** Entities that hold something: the instruments' names. */
  holds: string | null;
  /** Instruments: the holder, "unknown" when nobody holds it. */
  heldBy: string | null;
  /** Instruments: "Threat 3/5" or "Threat unrated". */
  threat: string | null;
  /** Entities with a recorded span: "On screen 0:02–0:09". */
  onScreen: string | null;
  /** Items with a track: the span the tracker followed them, "On screen 0:02–0:09 (tracked)", beside the report's own span. */
  trackedOnScreen: string | null;
  /** A person the run did not track: "Not tracked: <why>", from the agent's reason; null when tracked or unrecorded. */
  notTracked: string | null;
  /** The time the item is shown at, as a clock. */
  time: string | null;
  /** Badges for a reviewer's work: "Added by reviewer", then "Incomplete track" when the tracker lost the item. */
  badges: string[];
  /** Why Play from here is disabled; null when it can play. */
  playNote: string | null;
}

export function evidenceDetailLines(item: EvidenceDetailItem): EvidenceDetailLines {
  const isInstrument = item.kind === 'instrument';
  return {
    description: item.description.trim() || 'No description was recorded.',
    holds: item.kind === 'entity' && item.holds.length > 0 ? item.holds.join(', ') : null,
    heldBy: isInstrument ? (item.holder ?? 'unknown') : null,
    threat: isInstrument ? `Threat ${item.threatLevel === null ? 'unrated' : `${item.threatLevel}/5`}` : null,
    onScreen:
      item.kind === 'entity' && item.onScreen
        ? `On screen ${formatClock(item.onScreen.firstSeenSeconds)}–${formatClock(item.onScreen.lastSeenSeconds)}`
        : null,
    trackedOnScreen:
      item.evidence?.track && item.evidence.trackFirstSeenSeconds !== null && item.evidence.trackLastSeenSeconds !== null
        ? `On screen ${formatClock(item.evidence.trackFirstSeenSeconds)}–${formatClock(item.evidence.trackLastSeenSeconds)} (tracked)`
        : null,
    notTracked:
      item.kind === 'entity' && !item.evidence?.track && item.evidence?.trackGap ? `Not tracked: ${item.evidence.trackGap}` : null,
    time: item.seconds === null ? null : formatClock(item.seconds),
    badges: [
      ...(item.evidence?.addedByReviewer ? ['Added by reviewer'] : []),
      ...(item.evidence?.track && item.evidence.trackPartial ? ['Incomplete track'] : []),
    ],
    playNote: item.seconds === null ? 'No time was recorded for this item, so it cannot be played from here.' : null,
  };
}

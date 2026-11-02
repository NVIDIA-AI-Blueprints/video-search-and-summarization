// SPDX-License-Identifier: Apache-2.0

// A stored revision payload (the incident-contract-v3 report, read leniently:
// a human revision holds what a reviewer typed) as the structured shapes the
// report view shows, each item joined to its evidence entry. Shared by the
// Supabase and in-memory adapters so both read a payload the same way.

import { canonicalEntityType, canonicalIncidentType, parseTimestamp } from '@/modules/reports/domain/normalize';
import type {
  ItemEvidence,
  StructuredAsset,
  StructuredEntity,
  StructuredIncident,
  StructuredInstrument,
} from '@/modules/reports/domain/report-view';
import type { EvidenceEntry, EvidenceItemType, ReportPayload } from '@/modules/reports/domain/revision';

type Row = Record<string, unknown>;

const text = (value: unknown): string | null => (typeof value === 'string' && value.trim() ? value : null);
const num = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null);
const asRow = (value: unknown): Row => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Row) : {});
const rows = (value: unknown): Row[] => (Array.isArray(value) ? value.map(asRow) : []);
const byId = (key: string) => (a: Row, b: Row) => String(a[key] ?? '').localeCompare(String(b[key] ?? ''), undefined, { numeric: true });

/** The entry's track: a key and both span ends, else none at all (a half-written track is read as no track). */
type TrackFields = Pick<
  ItemEvidence,
  'track' | 'trackUrl' | 'trackFirstSeenSeconds' | 'trackLastSeenSeconds' | 'trackSource' | 'trackPartial'
>;
function trackOf(entry: EvidenceEntry): TrackFields {
  const track = text(entry.track);
  const first = num(entry.track_first_seen_seconds);
  const last = num(entry.track_last_seen_seconds);
  if (!track || first === null || last === null || last < first)
    return { track: null, trackUrl: null, trackFirstSeenSeconds: null, trackLastSeenSeconds: null, trackSource: null, trackPartial: false };
  return {
    track,
    trackUrl: null,
    trackFirstSeenSeconds: first,
    trackLastSeenSeconds: last,
    trackSource: entry.track_source === 'reviewer' ? 'reviewer' : null,
    trackPartial: entry.track_partial === true,
  };
}

function evidenceOf(entries: EvidenceEntry[], type: EvidenceItemType, itemId: string): ItemEvidence | null {
  const entry = entries.find((candidate) => candidate.item_type === type && candidate.item_id === itemId);
  if (!entry) return null;
  return {
    image: entry.image,
    imageUrl: null,
    frame: text(entry.frame),
    frameUrl: null,
    located: typeof entry.located === 'boolean' ? entry.located : null,
    boxSeconds: num(entry.box_seconds),
    imageGap: text(entry.image_gap),
    boundingBox: entry.bounding_box,
    timestampSeconds: entry.timestamp_seconds,
    uploaded: entry.source === 'upload',
    clipUrl: null,
    ...trackOf(entry),
    addedByReviewer: entry.source === 'reviewer',
  };
}

export interface StructuredReport {
  incident: StructuredIncident;
  entities: StructuredEntity[];
  instruments: StructuredInstrument[];
  assets: StructuredAsset[];
}

export function structuredFromPayload(payload: ReportPayload, evidence: EvidenceEntry[]): StructuredReport {
  const incident = asRow(payload.incident);
  return {
    incident: {
      type: canonicalIncidentType(incident.type),
      startSeconds: parseTimestamp(incident.start_timestamp),
      endSeconds: parseTimestamp(incident.end_timestamp),
      duration: num(incident.duration),
      description: text(incident.description),
      severityLevel: num(incident.severity_level),
      confidenceScore: num(incident.confidence_score),
    },
    entities: rows(payload.entities)
      .sort(byId('entity_id'))
      .map((row) => {
        const entityId = String(row.entity_id ?? '');
        return {
          entityId,
          type: canonicalEntityType(row.type),
          description: String(row.description ?? ''),
          timestampSeconds: num(row.timestamp_seconds),
          firstSeenSeconds: num(row.first_seen_seconds),
          lastSeenSeconds: num(row.last_seen_seconds),
          label: String(row.label ?? ''),
          evidence: evidenceOf(evidence, 'entity', entityId),
        };
      }),
    instruments: rows(payload.instruments)
      .sort(byId('instrument_id'))
      .map((row) => {
        const instrumentId = String(row.instrument_id ?? '');
        return {
          instrumentId,
          entityId: text(row.entity_id),
          name: String(row.name ?? ''),
          description: String(row.description ?? ''),
          threatLevel: num(row.threat_level),
          timestampSeconds: num(row.timestamp_seconds),
          label: String(row.label ?? ''),
          evidence: evidenceOf(evidence, 'instrument', instrumentId),
        };
      }),
    assets: rows(payload.assets)
      .sort(byId('asset_id'))
      .map((row) => {
        const assetId = String(row.asset_id ?? '');
        return {
          assetId,
          name: String(row.name ?? ''),
          description: String(row.description ?? ''),
          timestampSeconds: num(row.timestamp_seconds),
          label: String(row.label ?? ''),
          evidence: evidenceOf(evidence, 'asset', assetId),
        };
      }),
  };
}

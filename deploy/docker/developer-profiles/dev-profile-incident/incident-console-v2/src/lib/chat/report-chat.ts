// SPDX-License-Identifier: Apache-2.0

// What the report page's chat asks about, and the request it sends. Browser-safe.
// In View the chat asks about the version on screen (official or not); in Edit
// it asks about the unsaved draft, edited from that version.

import type { EditDraft } from '@/modules/reports/domain/edit-draft';
import type { RevisionSummary } from '@/modules/reports/domain/revision';
import type { StructuredAsset, StructuredEntity, StructuredInstrument } from '@/modules/reports/domain/report-view';

export interface ChatTarget {
  /** The saved version the question is about (the draft's base when there is a draft). */
  versionId: number;
  /** The unsaved draft as an incident report, only while editing. */
  draftReport: Record<string, unknown> | null;
  /** Shown on the chip and under each answer, e.g. "v2 · Human · not official". */
  label: string;
}

export function chatTarget({
  revision,
  isOfficial,
  draft,
}: {
  revision: Pick<RevisionSummary, 'revisionId' | 'revisionNo' | 'kind'>;
  isOfficial: boolean;
  draft: EditDraft | null;
}): ChatTarget {
  if (draft)
    return { versionId: revision.revisionId, draftReport: draftReport(draft), label: `Draft from v${revision.revisionNo} · unsaved` };
  return {
    versionId: revision.revisionId,
    draftReport: null,
    label: `v${revision.revisionNo} · ${revision.kind === 'human' ? 'Human' : 'AI'} · ${isOfficial ? 'official' : 'not official'}`,
  };
}

/**
 * The draft in the agent's incident report shape. Unlike a save it needs no valid
 * draft: half-filled values are left out. An item that came from the opened
 * version keeps its id there, so the agent can find its saved crop; a new item
 * gets an id of its own.
 */
export function draftReport(draft: EditDraft): Record<string, unknown> {
  const entityIds = new Map(draft.entities.map((entity, index) => [entity.key, entity.sourceId ?? `new-E${index + 1}`]));
  const seen = (item: { timestampSeconds: number | null }) =>
    item.timestampSeconds === null ? {} : { timestamp_seconds: item.timestampSeconds };
  const { incident } = draft;
  return {
    incident: {
      ...(incident.type ? { type: incident.type } : {}),
      ...(incident.start_timestamp !== null ? { start_timestamp: incident.start_timestamp } : {}),
      ...(incident.end_timestamp !== null ? { end_timestamp: incident.end_timestamp } : {}),
      description: incident.description,
      ...(incident.severity_level !== null ? { severity_level: incident.severity_level } : {}),
    },
    entities: draft.entities.map((entity) => ({
      entity_id: entityIds.get(entity.key),
      type: entity.type,
      description: entity.description,
      label: entity.label,
      ...seen(entity),
    })),
    instruments: draft.instruments.map((instrument, index) => ({
      instrument_id: instrument.sourceId ?? `new-I${index + 1}`,
      entity_id: (instrument.holderKey && entityIds.get(instrument.holderKey)) || null,
      name: instrument.name,
      description: instrument.description,
      label: instrument.label,
      ...(instrument.threat_level !== null ? { threat_level: instrument.threat_level } : {}),
      ...seen(instrument),
    })),
    assets: draft.assets.map((asset, index) => ({
      asset_id: asset.sourceId ?? `new-A${index + 1}`,
      name: asset.name,
      description: asset.description,
      label: asset.label,
      ...seen(asset),
    })),
  };
}

/** The body of POST /api/chat for one question. */
export function chatRequestBody({
  messages,
  sessionId,
  videoId,
  target,
}: {
  messages: Array<{ role: 'user' | 'assistant'; content: string }>;
  sessionId?: string;
  videoId: string;
  target: ChatTarget;
}) {
  return {
    messages,
    ...(sessionId ? { sessionId } : {}),
    incidentId: videoId,
    versionId: target.versionId,
    ...(target.draftReport ? { draftReport: target.draftReport } : {}),
  };
}

/**
 * How each cited item is named under an answer: the label or name its evidence card shows ("Person in light shirt",
 * "Cash register"), never its id. Ids are internal references that stay in the citation data.
 */
export function citationTitles(
  report: { entities: StructuredEntity[]; instruments: StructuredInstrument[]; assets: StructuredAsset[] } | null,
): Record<string, string> {
  if (!report) return {};
  const titles: Record<string, string> = {};
  const name = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
  for (const entity of report.entities) titles[entity.entityId] = name(entity.label || entity.type || 'person');
  for (const item of report.instruments) titles[item.instrumentId] = name(item.name || item.label || 'instrument');
  for (const item of report.assets) titles[item.assetId] = name(item.name || item.label || 'asset');
  return titles;
}

/** A citation's title: its item's name from `titles`, else its kind ("Instrument"); never its id. */
export function citationTitle(titles: Record<string, string>, citation: { itemId: string; itemType: string }): string {
  return titles[citation.itemId] ?? citation.itemType.charAt(0).toUpperCase() + citation.itemType.slice(1);
}

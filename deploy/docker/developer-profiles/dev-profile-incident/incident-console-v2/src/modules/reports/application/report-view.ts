// SPDX-License-Identifier: Apache-2.0

// Server-only builder for the report view (types in lib/reports/report-view.ts).
// Its input is the translated RevisionRecord (lib/ports/incidents.ts); the
// Supabase rows are mapped by lib/adapters/supabase/mappers/report.ts.

import type { RevisionRecord } from '@/modules/reports/domain/IncidentRepository';
import { canonicalIncidentType, parseTimestamp } from '@/modules/reports/domain/normalize';
import type { ModelOutput, ReportView, RunDetails } from '@/modules/reports/domain/report-view';
import type { ReportPayload } from '@/modules/reports/domain/revision';

export { reportHeading } from '@/modules/reports/domain/report-view';
export type { ReportView } from '@/modules/reports/domain/report-view';

const text = (value: unknown): string | null => (typeof value === 'string' && value.trim() ? value : null);
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
/** The stored payload's list rows, reduced to the named fields (raw keys), so two payloads compare field by field. */
const fieldsOf = (value: unknown, fields: string[]) =>
  Array.isArray(value) ? value.map((row) => fields.map((field) => (row as Record<string, unknown> | null)?.[field])) : [];

/**
 * `baseline` is the stored payload of the AI revision the view's revision derives from (its own payload for an AI
 * revision). Items are compared with it, not with the model's first pass: the zoomed second look rewrites item
 * names and descriptions in the stored AI revision, so only a change made after that is a reviewer edit.
 */
export function toReportView(
  record: RevisionRecord,
  playbackUrl: string,
  baseline: ReportPayload | null = record.revision.kind === 'ai' ? record.payload : null,
): ReportView {
  const structured: ReportView['structured'] = {
    incident: record.incident,
    entities: record.entities,
    instruments: record.instruments,
    assets: record.assets,
  };

  // The model's own report and the run's request/response, from the immutable run notes; a
  // revision whose run is not recorded has none.
  const notes = record.modelRun?.notes ?? { kind: 'none' as const };
  let modelOutput: ModelOutput | null = null;
  if (notes.kind === 'contract') {
    const { report } = notes;
    modelOutput = {
      source: 'contract',
      title: report.incident.title,
      location: report.incident.location,
      severityReason: report.incident.severity_reason,
      original: {
        type: report.incident.type,
        severityLevel: report.incident.severity_level,
        startSeconds: report.incident.start_timestamp,
        endSeconds: report.incident.end_timestamp,
        description: report.incident.description,
      },
    };
  } else if (notes.kind === 'legacy') {
    const { report } = notes;
    modelOutput = {
      source: 'legacy',
      title: text(report.title),
      location: text(report.location),
      severityReason: text(report.severity_reason),
      original: {
        type: canonicalIncidentType(report.incident_type),
        severityLevel: report.severity,
        startSeconds: parseTimestamp(report.incident_start),
        endSeconds: parseTimestamp(report.incident_end),
        description: text(report.description),
      },
    };
  }

  const differences: ReportView['differences'] = { fields: [] };
  if (modelOutput) {
    const { original } = modelOutput;
    const current = structured.incident;
    if (original.type && current.type && original.type !== current.type) {
      differences.type = { model: original.type, current: current.type };
      differences.fields.push('type');
    }
    if (original.severityLevel !== null && current.severityLevel !== null && original.severityLevel !== current.severityLevel) {
      differences.severity = { model: original.severityLevel, current: current.severityLevel };
      differences.fields.push('severity');
    }
    if (original.description !== null && current.description !== null && original.description !== current.description)
      differences.fields.push('description');
    if (notes.kind === 'contract') {
      // Legacy start/end were strings the old editor rewrote freely, so only
      // contract runs compare timestamps exactly.
      if (original.startSeconds !== current.startSeconds || original.endSeconds !== current.endSeconds)
        differences.fields.push('incident window');
    }
  }
  if (baseline) {
    const stored = record.payload;
    if (
      !same(
        fieldsOf(baseline.entities, ['entity_id', 'type', 'description']),
        fieldsOf(stored.entities, ['entity_id', 'type', 'description']),
      )
    )
      differences.fields.push('entities');
    if (
      !same(
        fieldsOf(baseline.instruments, ['instrument_id', 'entity_id', 'name', 'description', 'threat_level']),
        fieldsOf(stored.instruments, ['instrument_id', 'entity_id', 'name', 'description', 'threat_level']),
      )
    )
      differences.fields.push('instruments');
    if (!same(fieldsOf(baseline.assets, ['asset_id', 'name', 'description']), fieldsOf(stored.assets, ['asset_id', 'name', 'description'])))
      differences.fields.push('assets');
  }

  const { videoId } = record.revision;
  const modelRunId = record.revision.modelRunId;
  const run: RunDetails = {
    videoId,
    modelRunId,
    filename: record.video.filename,
    playbackUrl,
    model: record.modelRun?.modelName ?? 'Unknown model',
    promptVersion: record.modelRun?.promptVersion ?? null,
    generatedAt: record.revision.createdAt ?? record.modelRun?.runAt ?? '',
    notesKind: notes.kind,
  };
  if (notes.kind === 'contract' || notes.kind === 'invalid-contract') {
    run.request = notes.request;
    run.response = notes.response;
    run.rawModelOutput = notes.response.content ?? undefined;
    if (notes.kind === 'contract') {
      run.originalReport = notes.report;
      run.status = notes.status;
      run.statusInferred = notes.statusInferred;
      run.firstPassViolations = notes.validation.firstPass;
      const { firstPass: _firstPass, ...validation } = notes.validation;
      run.validation = validation;
      run.repair = notes.repair;
    } else run.notesError = notes.error;
  } else if (notes.kind === 'failed-attempt') {
    // Unreachable through the report route (a failed attempt has no incident),
    // but never presented as a report either.
    run.request = notes.request;
    run.status = notes.status;
    run.notesError = notes.failure ? `${notes.failure.code}: ${notes.failure.message}` : notes.status;
  } else if (notes.kind === 'legacy') {
    run.rawModelOutput = notes.rawModelOutput;
    run.legacyReviewerEdits = notes.legacyReviewerEdits;
  }

  return {
    videoId,
    revisionId: record.revision.revisionId,
    modelRunId,
    structured,
    modelOutput,
    run,
    revision: record.revision,
    official: { officialRevisionId: record.video.officialRevisionId, isOfficial: record.revision.isOfficial },
    differences,
  };
}

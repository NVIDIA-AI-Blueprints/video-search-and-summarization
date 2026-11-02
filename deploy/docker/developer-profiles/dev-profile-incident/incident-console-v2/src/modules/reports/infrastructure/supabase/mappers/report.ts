// SPDX-License-Identifier: Apache-2.0

// Row -> domain translation for one report revision (report_revisions, with
// its video and model_runs rows) and the save_human_revision result. The only
// place that knows those column and result names. The payload is the stored
// incident-contract-v3 report, read leniently: a human revision may hold what
// a reviewer typed.

import type { RevisionRecord } from '@/modules/reports/domain/IncidentRepository';
import { structuredFromPayload } from '@/modules/reports/domain/payload-view';
import { evidenceEntries, type RevisionKind, type RevisionSummary } from '@/modules/reports/domain/revision';
import { parseRunNotes } from '@/modules/reports/domain/run-notes';

type Row = Record<string, unknown>;

export interface RevisionRows {
  video: Row;
  revision: Row;
  /** model_runs row of revision.model_run_id; null when it names none or is not recorded. */
  modelRun: Row | null;
}

const text = (value: unknown): string | null => (typeof value === 'string' && value.trim() ? value : null);
const num = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null);
const asRow = (value: unknown): Row => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Row) : {});

/** The columns a History row needs: everything but the payload and evidence. */
export const REVISION_SUMMARY_COLUMNS = 'id,video_id,revision_no,kind,model_run_id,parent_id,created_by,created_at,note';

export function revisionSummaryFromRow(row: Row, officialRevisionId: number | null, model: string | null): RevisionSummary {
  const id = Number(row.id);
  return {
    revisionId: id,
    videoId: String(row.video_id),
    revisionNo: Number(row.revision_no),
    kind: (row.kind === 'human' ? 'human' : 'ai') as RevisionKind,
    createdBy: text(row.created_by),
    createdAt: text(row.created_at),
    note: text(row.note),
    parentId: num(row.parent_id),
    modelRunId: text(row.model_run_id),
    model,
    isOfficial: officialRevisionId !== null && officialRevisionId === id,
  };
}

/** save_human_revision's result -> RevisionSummary (the model is not part of it, so it is unknown here). */
export function savedRevisionFromRpc(result: unknown, officialRevisionId: number | null = null): RevisionSummary {
  const row = asRow(result);
  return revisionSummaryFromRow(
    {
      id: row.revisionId,
      video_id: row.videoId,
      revision_no: row.revisionNo,
      kind: row.kind,
      created_by: row.createdBy,
      created_at: row.createdAt,
      note: row.note,
      parent_id: row.parentId,
      model_run_id: row.modelRunId,
    },
    officialRevisionId,
    null,
  );
}

export function revisionRecordFromRows(input: RevisionRows): RevisionRecord {
  const { video, revision, modelRun } = input;
  const payload = asRow(revision.payload);
  const evidence = evidenceEntries(revision.evidence);
  const officialRevisionId = num(video.official_revision_id);
  const videoId = String(video.id);
  const modelName = modelRun ? text(modelRun.model_name) : null;
  return {
    revision: revisionSummaryFromRow(revision, officialRevisionId, modelName),
    payload,
    evidence,
    video: {
      storageKey: typeof video.filepath === 'string' && video.filepath ? video.filepath : null,
      filename: (text(video.filepath) ?? '').split('/').pop() || videoId,
      officialRevisionId,
    },
    modelRun: modelRun
      ? {
          modelName,
          promptVersion: text(modelRun.prompt_version),
          runAt: text(modelRun.run_datetime),
          notes: parseRunNotes(modelRun.notes),
        }
      : null,
    ...structuredFromPayload(payload, evidence),
  };
}

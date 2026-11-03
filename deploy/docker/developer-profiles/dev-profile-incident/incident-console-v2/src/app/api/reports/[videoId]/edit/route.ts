// SPDX-License-Identifier: Apache-2.0

import { NextResponse } from 'next/server';
import { z } from 'zod';

import { getDbAdapters } from '@/lib/container';
import { fetchEvidenceItemEmbeddings } from '@/modules/reports/infrastructure/agent/evidence-embeddings';
import { saveHumanEdit, SearchableStepError } from '@/modules/reports/application/save-human-edit';
import { StructuredEditError, validateStructuredEdit } from '@/modules/contract/domain/structured-edit';
import type { StructuredEdit } from '@/modules/reports/domain/edit-draft';
import { getServiceConfiguration, isR2Configured } from '@/lib/env';
import { R2ReviewerEvidenceStore } from '@/modules/reports/infrastructure/r2/reviewer-evidence-store';
import { errorResponse, rejectionResponse } from '@/lib/http';

// Save: structured (Class A) reviewer edits become a new human revision whose
// parent is the revision the reviewer opened (parentId). Earlier revisions and
// model_runs.notes (original model output, raw response, repair record,
// outcome) are never modified. Class B model output and Class C provenance are
// rejected before anything reaches the database.
const requestSchema = z
  .object({
    parentId: z.number().int().positive(),
    editedBy: z.string().trim().min(1).max(256),
    note: z.string().trim().max(2000).nullish(),
    edit: z.unknown(),
  })
  .strict();

export async function PATCH(request: Request, context: { params: Promise<{ videoId: string }> }) {
  try {
    const { videoId } = await context.params;
    const parsed = requestSchema.safeParse(await request.json().catch(() => null));
    if (!parsed.success) {
      return NextResponse.json({ error: 'The request must contain parentId, editedBy, edit and optionally note only' }, { status: 400 });
    }
    let edit: StructuredEdit;
    try {
      edit = validateStructuredEdit(parsed.data.edit);
    } catch (error) {
      if (error instanceof StructuredEditError) {
        return NextResponse.json(
          { error: error.message, violations: error.violations, notEditable: error.notEditable },
          { status: error.status },
        );
      }
      throw error;
    }

    const config = getServiceConfiguration();
    // With no official revision the new revision is the searched one: its crops are embedded and written with it.
    const revision = await saveHumanEdit(
      getDbAdapters(config),
      { videoId, parentId: parsed.data.parentId, edit, createdBy: parsed.data.editedBy, note: parsed.data.note },
      (evidence) => fetchEvidenceItemEmbeddings(config, evidence),
      isR2Configured(config) ? new R2ReviewerEvidenceStore(config) : undefined,
    );
    return NextResponse.json({ saved: true, revision });
  } catch (error) {
    if (error instanceof SearchableStepError) return NextResponse.json({ error: error.message }, { status: 502 });
    return rejectionResponse(error) ?? errorResponse(error, 'Could not save the edit');
  }
}

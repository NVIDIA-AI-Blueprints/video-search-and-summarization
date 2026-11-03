// SPDX-License-Identifier: Apache-2.0

// Analyse one uploaded video: the whole of what an upload's analysis does, so
// POST /api/analysis and the background job loop (lib/jobs/loop.ts) run the
// same code. The video is new: it gets a fresh id unless the caller names one
// (a job's id, see jobVideoId); the video row is created once and never reset.
// The AI revision is stored, then its evidence crops so the video is searchable at once.

import { compactId } from '@/lib/ids';
import { type AnalysisMode, type AnalysisPorts, runContractAnalysis } from '@/modules/analysis/application/run-contract-analysis';
import { type SearchableCropsResult, storeSearchableCrops } from '@/modules/reports/application/store-searchable-crops';
import type { EmbedRevisionEvidence } from '@/modules/reports/application/types';
import type { DbAdapters } from '@/modules/shared/domain/adapters';

export interface UploadAnalysisInput {
  /** The uploaded video's R2 key. */
  r2Key: string;
  /**
   * The video id to use; omitted mints a fresh one. A background job passes the
   * same id on every attempt, so a re-run after a restart or a retry adds to the
   * one video instead of leaving a second video row on the same R2 object.
   */
  videoId?: string;
  /** The model id; empty or missing runs the default model. */
  model?: string | null;
  additionalInstruction?: string;
}

export interface UploadAnalysisDeps {
  ports: AnalysisPorts;
  mode: AnalysisMode;
  db: Pick<DbAdapters, 'incidents' | 'videos'>;
  embed: EmbedRevisionEvidence;
  defaultModel: string;
  now: () => Date;
}

/** What the upload flow needs to open the new run's report page. */
export interface AnalysisRunSummary {
  videoId: string;
  modelRunId: string;
  /** The new AI revision. */
  revisionId: number;
  model: string;
  promptVersion: string;
  /** valid_first_pass or valid_after_structural_repair; failures are thrown. */
  outcome: string;
  /** Evidence crops stored so the video is searchable before it has an official revision. */
  searchable: SearchableCropsResult;
}

export async function analyseUpload(input: UploadAnalysisInput, deps: UploadAnalysisDeps): Promise<AnalysisRunSummary> {
  const { run, revisionId } = await runContractAnalysis(
    {
      videoId: input.videoId || compactId('v', deps.ports.randomUUID()),
      r2Key: input.r2Key,
      model: input.model || deps.defaultModel,
      additionalInstruction: input.additionalInstruction,
      newVideo: { uploadedAt: deps.now().toISOString() },
    },
    deps.ports,
    deps.mode,
  );
  const searchable = await storeSearchableCrops(deps.db, run.videoId, revisionId, deps.embed);
  return {
    videoId: run.videoId,
    modelRunId: run.modelRunId,
    revisionId,
    model: run.request.model,
    promptVersion: run.contractVersion,
    outcome: run.status,
    searchable,
  };
}

// SPDX-License-Identifier: Apache-2.0

// Re-blur one video's evidence: the face-blur toggle's job. Switching the toggle changes how the evidence images look,
// not what the report says, so no analysis runs. The agent (POST /api/v1/incidents/{id}/reblur) keeps the report
// revision the page shows, cuts every evidence frame and crop again from the stored video at the same seconds and boxes,
// blurs them (or not), and stores the result as a new AI revision with the same report. Only the head detector is
// called, and only when blurring. This use case then records the new model run (a contract record whose report is the
// kept one, so the run's details page reads as before), checks the revision, and makes the video's crops searchable
// like a fresh analysis. Earlier revisions and their images are never touched.

import { compactId } from '@/lib/ids';
import { p1InferenceConfig } from '@/modules/analysis/application/build-request';
import type { AnalysisRunSummary, UploadAnalysisDeps } from '@/modules/analysis/application/analyse-upload';
import type { ContractRunRecord } from '@/modules/analysis/domain/AnalysisRunRepository';
import { AgentRequestError, type AgentHttpResponse } from '@/modules/analysis/domain/IncidentAgent';
import type { DerivedContractReport } from '@/modules/contract/domain/types';
import { storeSearchableCrops } from '@/modules/reports/application/store-searchable-crops';

/**
 * How long the console waits for the agent. Head detection takes seconds; the agent stops its own work earlier
 * (REBLUR_DEADLINE_SECONDS, 480 s, with a 504 and nothing stored), so a call this side gave up on has written no revision.
 */
export const REBLUR_TIMEOUT_SECONDS = 600;

export interface ReblurInput {
  videoId: string;
  /** The video's R2 key. */
  r2Key: string;
  /** The job's model, used only to label the run when the source run names none. */
  model?: string | null;
  /** Blur every head in the new images (true) or store them unblurred (false). */
  blurFaces: boolean;
}

/** The FastAPI error detail of an agent error body, or the body itself. */
function agentErrorMessage(response: AgentHttpResponse): string {
  try {
    const detail = (JSON.parse(response.text) as { detail?: unknown }).detail;
    if (typeof detail === 'string' && detail) return detail;
  } catch {
    // not JSON: fall through
  }
  return response.text || response.statusText;
}

const asObject = (value: unknown): Record<string, unknown> | undefined =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined;

export async function reblurVideo(input: ReblurInput, deps: UploadAnalysisDeps): Promise<AnalysisRunSummary> {
  const { ports } = deps;
  let operation = 'reading the report to keep';
  try {
    if (!ports.agent) throw new Error('INCIDENT_AGENT_BASE_URL is not configured');
    const source = await deps.db.incidents.getRevision(input.videoId, null);
    if (!source) throw new Error('This video has no report revision to re-blur');
    const sourceNotes = source.modelRun?.notes.kind === 'contract' ? source.modelRun.notes : null;

    operation = 'verifying the R2 video object';
    const { contentLength } = await ports.videos.verify(input.r2Key);
    operation = 'creating the signed R2 video URL';
    const playbackUrl = await ports.videos.playbackUrl(input.r2Key);
    operation = 'reading the video length';
    const videoDurationSeconds = await ports.videos.durationSeconds(input.r2Key, contentLength);

    const attemptedAt = deps.now().toISOString();
    const modelRunId = compactId('m', `${input.videoId}:${attemptedAt}:${ports.randomUUID()}`);
    const model = sourceNotes?.request.model || source.modelRun?.modelName || input.model || deps.defaultModel;

    operation = 'calling the incident agent';
    let agentResponse: AgentHttpResponse;
    try {
      agentResponse = await ports.agent.reblur(
        {
          videoId: input.videoId,
          videoKey: input.r2Key,
          videoUrl: playbackUrl,
          sourceRevisionId: source.revision.revisionId,
          modelRunId,
          model,
          blurFaces: input.blurFaces,
          videoDurationSeconds,
        },
        AbortSignal.timeout(REBLUR_TIMEOUT_SECONDS * 1000),
      );
    } catch (error) {
      if (!(error instanceof AgentRequestError)) throw error;
      throw new Error(`The agent request failed (${error.code}): ${error.message}`);
    }
    if (!agentResponse.ok) throw new Error(`The agent returned HTTP ${agentResponse.status}: ${agentErrorMessage(agentResponse)}`);
    const reply = asObject(JSON.parse(agentResponse.text));
    const revisionId = reply?.revision_id;
    if (typeof revisionId !== 'number' || !Number.isInteger(revisionId)) throw new Error('The agent stored no revision');
    const privacy = asObject(reply?.privacy);

    // The run record: the kept report with the source run's request, and the agent's privacy summary. No model response,
    // validation or provenance is copied: nothing was generated or validated by this run.
    const catalog = ports.catalog;
    const run: ContractRunRecord = {
      status: 'valid_first_pass',
      stage: null,
      failure: null,
      contractVersion: sourceNotes?.contractVersion ?? catalog.contractVersion(),
      videoId: input.videoId,
      r2Key: input.r2Key,
      modelRunId,
      attemptedAt,
      generatedAt: attemptedAt,
      request: {
        model,
        inferenceConfig: sourceNotes?.request.inferenceConfig ?? p1InferenceConfig(catalog),
        additionalInstruction: sourceNotes?.request.additionalInstruction ?? null,
        promptSha256: sourceNotes?.request.promptSha256 ?? catalog.promptSha256(),
        schemaSha256: sourceNotes?.request.schemaSha256 ?? catalog.schemaSha256(),
        videoDurationSeconds: videoDurationSeconds ?? sourceNotes?.request.videoDurationSeconds ?? null,
        ...(input.blurFaces ? { blurFaces: true as const } : {}),
      },
      response: privacy ? { privacy } : {},
      validation: { firstPass: [] },
      repair: null,
      reblur: { sourceRevisionId: source.revision.revisionId, sourceModelRunId: source.revision.modelRunId },
      report: source.payload as unknown as DerivedContractReport,
    };
    const savedRevisionId = await ports.runs.saveRun(run, { revisionId, evidence: null }, (next) => {
      operation = next;
    });
    operation = 'making the new evidence crops searchable';
    const searchable = await storeSearchableCrops(deps.db, input.videoId, savedRevisionId, deps.embed);
    return {
      videoId: input.videoId,
      modelRunId,
      revisionId: savedRevisionId,
      model,
      promptVersion: run.contractVersion,
      outcome: run.status,
      searchable,
    };
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(`${operation} failed: ${detail}`);
  }
}

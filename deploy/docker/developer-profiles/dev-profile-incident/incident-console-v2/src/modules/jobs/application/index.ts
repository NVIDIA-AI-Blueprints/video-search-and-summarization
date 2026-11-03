// SPDX-License-Identifier: Apache-2.0

// The background analysis queue's use cases: enqueue an uploaded video, list,
// retry and cancel jobs (the /api/jobs routes), and run the next job (one tick
// of the loop in lib/jobs/loop.ts). Analysis itself is injected, so a job runs
// exactly what POST /api/analysis runs (modules/analysis/application/analyse-upload.ts).

import { compactId } from '@/lib/ids';
import { resolveAllowedModel } from '@/modules/analysis/application/run-contract-analysis';
import type { ContractCatalog } from '@/modules/contract/domain/ContractCatalog';
import type { AnalysisJobRepository } from '@/modules/jobs/domain/AnalysisJobRepository';
import {
  type AnalysisJob,
  DEFAULT_FAILURE,
  HEARTBEAT_SECONDS,
  type JobOutcome,
  RECENT_FINISHED,
  STALE_SECONDS,
} from '@/modules/jobs/domain/job';
import { StoreRejection } from '@/modules/shared/domain/common';

export interface EnqueueInput {
  filePath?: unknown;
  filename?: unknown;
  model?: unknown;
}

/**
 * Queues one uploaded video. The model is checked now (an unsupported one is
 * refused at once, as POST /api/analysis does) and stored, so the job runs the
 * model chosen at upload even if the default changes meanwhile.
 */
export async function enqueueAnalysisJob(
  jobs: AnalysisJobRepository,
  catalog: ContractCatalog,
  input: EnqueueInput,
  defaultModel: string,
): Promise<AnalysisJob> {
  const filePath = typeof input.filePath === 'string' ? input.filePath.trim() : '';
  const filename = typeof input.filename === 'string' ? input.filename.trim() : '';
  if (!filePath || !filename) throw new StoreRejection('invalid', 'filepath and filename are required');
  const chosen = typeof input.model === 'string' && input.model.trim() ? input.model.trim() : defaultModel;
  const model = resolveAllowedModel(chosen, catalog).id;
  return jobs.enqueue({ filePath, filename, model });
}

/**
 * The video every attempt of a job analyses into: fixed by the job, so a run
 * cut short by a restart, and a retry, reuse one video row instead of leaving
 * another on the same R2 object (the row is created once; later runs add revisions).
 */
export const jobVideoId = (job: Pick<AnalysisJob, 'id' | 'createdAt' | 'filePath'>): string =>
  compactId('v', `analysis-job:${job.id}:${job.createdAt}:${job.filePath}`);

export const listAnalysisJobs = (jobs: AnalysisJobRepository): Promise<AnalysisJob[]> => jobs.list(RECENT_FINISHED);

export async function retryAnalysisJob(jobs: AnalysisJobRepository, id: number): Promise<AnalysisJob> {
  const job = await jobs.retry(id);
  if (!job) throw new StoreRejection('invalid', 'Only a failed or cancelled job can be retried');
  return job;
}

export async function cancelAnalysisJob(jobs: AnalysisJobRepository, id: number): Promise<AnalysisJob> {
  const job = await jobs.cancel(id);
  if (!job) throw new StoreRejection('invalid', 'Only a queued job can be cancelled');
  return job;
}

export interface JobRunnerDeps {
  jobs: AnalysisJobRepository;
  /** Runs the job's analysis to the end; throws with the plain reason when it fails. */
  analyse(job: AnalysisJob): Promise<{ videoId: string; revisionId: number }>;
  /** Calls fn every ms until the returned function is called (setInterval in production). */
  every(ms: number, fn: () => void): () => void;
  /** Where a heartbeat or bookkeeping problem is reported; never thrown. */
  warn(message: string): void;
  staleSeconds?: number;
  heartbeatSeconds?: number;
}

const reason = (error: unknown): string => (error instanceof Error && error.message.trim() ? error.message.trim() : DEFAULT_FAILURE);

/**
 * One tick of the loop: claims the oldest queued job (recovering stale ones
 * first), heartbeats while it analyses, then stores done or failed. Returns
 * the finished job, or null when nothing was queued or the claim was lost
 * meanwhile. Throws only when the queue itself cannot be reached.
 */
export async function runNextJob(deps: JobRunnerDeps): Promise<AnalysisJob | null> {
  const job = await deps.jobs.claim(deps.staleSeconds ?? STALE_SECONDS);
  if (!job) return null;
  const attempt = job.attempts;
  let held = true;
  const stop = deps.every((deps.heartbeatSeconds ?? HEARTBEAT_SECONDS) * 1000, () => {
    if (!held) return;
    deps.jobs.heartbeat(job.id, attempt).then(
      (ok) => {
        if (!ok) {
          held = false;
          deps.warn(`analysis job ${job.id} is no longer this console's claim`);
        }
      },
      (error) => deps.warn(`heartbeat for analysis job ${job.id} failed: ${reason(error)}`),
    );
  });
  let outcome: JobOutcome;
  try {
    const report = await deps.analyse(job);
    outcome = { status: 'done', videoId: report.videoId, revisionId: report.revisionId };
  } catch (error) {
    outcome = { status: 'failed', error: reason(error) };
  } finally {
    stop();
  }
  const stored = await deps.jobs.finish(job.id, attempt, outcome);
  if (!stored) deps.warn(`analysis job ${job.id} finished after its claim was lost; its outcome was not stored`);
  return stored;
}

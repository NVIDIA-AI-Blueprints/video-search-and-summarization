// SPDX-License-Identifier: Apache-2.0

// AnalysisJobRepository: the background analysis queue. Every state change is
// conditional on the job's current state (and, for the loop, on its claim), so
// two loops or a late runner never overwrite each other. The rules are in ./job.ts.

import type { AnalysisJob, JobOutcome } from '@/modules/jobs/domain/job';

export interface NewAnalysisJob {
  filePath: string;
  filename: string;
  model: string | null;
}

export interface AnalysisJobRepository {
  enqueue(job: NewAnalysisJob): Promise<AnalysisJob>;
  /** Every queued and running job plus the latest `recent` finished ones (see jobsList). */
  list(recent: number): Promise<AnalysisJob[]>;
  /**
   * Recovers stale running jobs (see recoverStale), then claims the oldest
   * queued job, or returns null. Atomic: two loops never claim the same job.
   */
  claim(staleSeconds: number): Promise<AnalysisJob | null>;
  /** Heartbeat; false when the claim is no longer this runner's (recovered as stale, or finished). */
  heartbeat(id: number, attempt: number): Promise<boolean>;
  /** Stores the outcome when the claim still holds; null when it does not. */
  finish(id: number, attempt: number, outcome: JobOutcome): Promise<AnalysisJob | null>;
  /** A failed or cancelled job back to the queue; null when it is neither. */
  retry(id: number): Promise<AnalysisJob | null>;
  /** A queued job to cancelled; null when it is not queued. */
  cancel(id: number): Promise<AnalysisJob | null>;
}

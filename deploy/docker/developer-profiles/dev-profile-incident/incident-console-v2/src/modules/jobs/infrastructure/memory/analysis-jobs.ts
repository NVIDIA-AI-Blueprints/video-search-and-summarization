// SPDX-License-Identifier: Apache-2.0

// In-memory AnalysisJobRepository for use-case tests: the same rules as the
// database functions, from modules/jobs/domain/job.ts, on a clock the test sets.

import type { AnalysisJobRepository, NewAnalysisJob } from '@/modules/jobs/domain/AnalysisJobRepository';
import {
  type AnalysisJob,
  canCancel,
  canRetry,
  cancelled,
  claimed,
  finished,
  holdsClaim,
  type JobOutcome,
  jobsList,
  nextToClaim,
  recoverStale,
  retried,
} from '@/modules/jobs/domain/job';

export class InMemoryAnalysisJobRepository implements AnalysisJobRepository {
  readonly jobs = new Map<number, AnalysisJob>();
  now: Date;
  private nextId = 1;

  constructor(now = new Date('2026-10-31T08:00:00Z')) {
    this.now = now;
  }

  /** Moves the clock on. */
  advance(seconds: number): void {
    this.now = new Date(this.now.getTime() + seconds * 1000);
  }

  private put(job: AnalysisJob): AnalysisJob {
    this.jobs.set(job.id, job);
    return { ...job };
  }

  async enqueue(job: NewAnalysisJob): Promise<AnalysisJob> {
    return this.put({
      id: this.nextId++,
      ...job,
      status: 'queued',
      attempts: 0,
      error: null,
      createdAt: this.now.toISOString().replace('Z', ''),
      startedAt: null,
      finishedAt: null,
      heartbeatAt: null,
      videoId: null,
      revisionId: null,
    });
  }

  async list(recent: number): Promise<AnalysisJob[]> {
    return jobsList([...this.jobs.values()], recent).map((job) => ({ ...job }));
  }

  async claim(staleSeconds: number): Promise<AnalysisJob | null> {
    for (const job of this.jobs.values()) this.jobs.set(job.id, recoverStale(job, this.now, staleSeconds));
    const next = nextToClaim([...this.jobs.values()]);
    return next ? this.put(claimed(next, this.now)) : null;
  }

  async heartbeat(id: number, attempt: number): Promise<boolean> {
    const job = this.jobs.get(id);
    if (!job || !holdsClaim(job, attempt)) return false;
    this.put({ ...job, heartbeatAt: this.now.toISOString().replace('Z', '') });
    return true;
  }

  async finish(id: number, attempt: number, outcome: JobOutcome): Promise<AnalysisJob | null> {
    const job = this.jobs.get(id);
    return job && holdsClaim(job, attempt) ? this.put(finished(job, outcome, this.now)) : null;
  }

  async retry(id: number): Promise<AnalysisJob | null> {
    const job = this.jobs.get(id);
    return job && canRetry(job) ? this.put(retried(job)) : null;
  }

  async cancel(id: number): Promise<AnalysisJob | null> {
    const job = this.jobs.get(id);
    return job && canCancel(job) ? this.put(cancelled(job, this.now)) : null;
  }
}

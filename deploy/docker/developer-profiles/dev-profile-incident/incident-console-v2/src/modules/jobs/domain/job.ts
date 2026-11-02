// SPDX-License-Identifier: Apache-2.0

// An analysis job: one uploaded video waiting for, running or finished with
// its analysis. The rules here are the ones the database applies in
// supabase/migrations/20261031120000_analysis_jobs.sql (claim_analysis_job and
// friends); they are pure so the in-memory repository and the tests share them
// (tests/analysis-jobs.test.mjs). Browser-safe: the upload page imports the types.

export const JOB_STATUSES = ['queued', 'running', 'done', 'failed', 'cancelled'] as const;
export type JobStatus = (typeof JOB_STATUSES)[number];

export interface AnalysisJob {
  id: number;
  /** The uploaded video's R2 key. */
  filePath: string;
  filename: string;
  /** The model id chosen at upload; null runs the console's default. */
  model: string | null;
  status: JobStatus;
  /** Claims since the job was queued or last retried; a second stale claim fails it. */
  attempts: number;
  /** The plain reason a job failed. */
  error: string | null;
  /** Naive UTC ISO timestamps, as stored. */
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  heartbeatAt: string | null;
  /** The new video and AI revision once done (null again if the video was deleted later). */
  videoId: string | null;
  revisionId: number | null;
}

/** How the loop finished one claim. */
export type JobOutcome = { status: 'done'; videoId: string; revisionId: number } | { status: 'failed'; error: string };

/** Seconds without a heartbeat after which a running job counts as abandoned (its console stopped). */
export const STALE_SECONDS = 180;
/** How often the loop heartbeats a running job; well inside STALE_SECONDS. */
export const HEARTBEAT_SECONDS = 15;
/** A job that went stale on this many claims fails instead of queueing again. */
export const MAX_STALE_CLAIMS = 2;

export const STALE_ERROR = 'The console stopped twice while analysing this video';
export const DEFAULT_FAILURE = 'The analysis failed';

export const isFinished = (job: Pick<AnalysisJob, 'status'>): boolean =>
  job.status === 'done' || job.status === 'failed' || job.status === 'cancelled';
export const canRetry = (job: Pick<AnalysisJob, 'status'>): boolean => job.status === 'failed' || job.status === 'cancelled';
export const canCancel = (job: Pick<AnalysisJob, 'status'>): boolean => job.status === 'queued';

const time = (iso: string | null): number => (iso ? Date.parse(iso.endsWith('Z') ? iso : `${iso}Z`) : Number.NaN);
const stamp = (now: Date): string => now.toISOString().replace('Z', '');

/** Whether a running job has had no heartbeat (or start) for staleSeconds at `now`. */
export function isStale(job: AnalysisJob, now: Date, staleSeconds = STALE_SECONDS): boolean {
  if (job.status !== 'running') return false;
  const last = time(job.heartbeatAt ?? job.startedAt ?? job.createdAt);
  return Number.isFinite(last) && last < now.getTime() - staleSeconds * 1000;
}

/** A stale running job goes back to the queue after its first stale claim and fails after the second. */
export function recoverStale(job: AnalysisJob, now: Date, staleSeconds = STALE_SECONDS): AnalysisJob {
  if (!isStale(job, now, staleSeconds)) return job;
  return job.attempts >= MAX_STALE_CLAIMS
    ? { ...job, status: 'failed', error: STALE_ERROR, finishedAt: stamp(now), heartbeatAt: null }
    : { ...job, status: 'queued', error: null, finishedAt: null, heartbeatAt: null };
}

/** The job the next claim takes: the oldest queued one (ties by id), or null. */
export function nextToClaim(jobs: readonly AnalysisJob[]): AnalysisJob | null {
  const queued = jobs.filter((job) => job.status === 'queued');
  queued.sort((a, b) => time(a.createdAt) - time(b.createdAt) || a.id - b.id);
  return queued[0] ?? null;
}

export function claimed(job: AnalysisJob, now: Date): AnalysisJob {
  return {
    ...job,
    status: 'running',
    attempts: job.attempts + 1,
    startedAt: stamp(now),
    heartbeatAt: stamp(now),
    finishedAt: null,
    error: null,
  };
}

/** Whether `attempt` (the attempts value a claim returned) is still the job's running claim. */
export const holdsClaim = (job: AnalysisJob, attempt: number): boolean => job.status === 'running' && job.attempts === attempt;

export function finished(job: AnalysisJob, outcome: JobOutcome, now: Date): AnalysisJob {
  return outcome.status === 'done'
    ? {
        ...job,
        status: 'done',
        error: null,
        videoId: outcome.videoId,
        revisionId: outcome.revisionId,
        finishedAt: stamp(now),
        heartbeatAt: null,
      }
    : {
        ...job,
        status: 'failed',
        error: outcome.error.trim() || DEFAULT_FAILURE,
        videoId: null,
        revisionId: null,
        finishedAt: stamp(now),
        heartbeatAt: null,
      };
}

export function retried(job: AnalysisJob): AnalysisJob {
  return {
    ...job,
    status: 'queued',
    attempts: 0,
    error: null,
    startedAt: null,
    finishedAt: null,
    heartbeatAt: null,
    videoId: null,
    revisionId: null,
  };
}

export function cancelled(job: AnalysisJob, now: Date): AnalysisJob {
  return { ...job, status: 'cancelled', finishedAt: stamp(now) };
}

/** How many finished jobs the upload page lists after the queued and running ones. */
export const RECENT_FINISHED = 10;

/**
 * The upload page's jobs list: every running and queued job (oldest first, the
 * order they run in), then the latest RECENT_FINISHED finished ones, newest first.
 */
export function jobsList(jobs: readonly AnalysisJob[], recent = RECENT_FINISHED): AnalysisJob[] {
  const byCreated = [...jobs].sort((a, b) => time(a.createdAt) - time(b.createdAt) || a.id - b.id);
  const active = byCreated.filter((job) => !isFinished(job));
  const done = byCreated.filter(isFinished).reverse().slice(0, recent);
  return [...active.filter((job) => job.status === 'running'), ...active.filter((job) => job.status === 'queued'), ...done];
}

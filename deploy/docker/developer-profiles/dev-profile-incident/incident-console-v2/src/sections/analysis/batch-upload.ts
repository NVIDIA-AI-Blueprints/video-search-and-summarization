// SPDX-License-Identifier: Apache-2.0

/**
 * A batch upload: several files queued on the upload page. The browser
 * uploads and converts them strictly one after another; each converted file
 * then becomes a background analysis job (POST /api/jobs) that the console's
 * server analyses one at a time, so each file becomes its own video and
 * report, and closing the tab stops only a file whose bytes are still being
 * sent. A row follows its job's state from the polled jobs list (applyJobs).
 * Everything here is pure, so the queue's order, failure isolation, removal,
 * cancellation and job tracking are tested without a browser
 * (tests/batch-upload.test.mjs); hooks/use-batch-upload.ts wires it to React
 * and the console's routes.
 */

import type { AnalysisJob } from '@/modules/jobs/domain/job';

/** The steps a file runs through, in order. */
export const BATCH_STAGES = ['uploading', 'converting', 'analysing'] as const;
export type BatchStage = (typeof BATCH_STAGES)[number];

export const batchStageLabels: Record<BatchStage, string> = {
  uploading: 'Uploading',
  converting: 'Converting',
  analysing: 'Analysing',
};

/**
 * `queued`: not uploaded yet. `waiting`: uploaded and converted, its job queued for analysis.
 * `cancelled`: cancelled before it finished uploading (nothing stored) or before its job ran.
 */
export type BatchStatus = 'queued' | 'waiting' | BatchStage | 'done' | 'failed' | 'cancelled';

export interface BatchItem {
  /** Local key; the stored name is only known after the upload. */
  key: string;
  /** The file's own name until the upload answers, then the name it is stored under. */
  name: string;
  /** Bytes, or null for a row built from a job (the file is not in this tab). */
  size: number | null;
  status: BatchStatus;
  /** The step that failed, for a failed file. */
  failedAt: BatchStage | null;
  /** 0 to 100 while the bytes upload. */
  uploadProgress: number;
  error: string | null;
  /** Whether the console converted the file to H.264 (null until the upload answers). */
  converted: boolean | null;
  /** The new report, once the analysis has stored it. */
  report: { videoId: string; revisionId: number } | null;
  /** The background analysis job, once the converted file is queued; the row then follows the job. */
  jobId: number | null;
  /** When the job's analysis started (naive UTC, from the server), so a reopened page shows the real elapsed time. */
  analysingSince?: string | null;
}

export interface BatchDeps {
  /**
   * Uploads (and, on the server, converts) one file and returns its R2 key and stored name.
   * `onUploaded` fires once every byte is sent and the server is probing or converting.
   */
  upload(
    file: File,
    onProgress: (progress: number) => void,
    onUploaded: () => void,
  ): Promise<{ filePath: string; filename: string; converted: boolean }>;
  /** Queues the uploaded file's analysis as a background job. */
  enqueue(input: { filePath: string; filename: string; model: string }): Promise<AnalysisJob>;
}

/** The message a single upload rejects with when it is cancelled (hooks/use-video-upload.ts). */
export const UPLOAD_CANCELLED_MESSAGE = 'Upload was cancelled';

/** Shown when the upload step fails with no message of its own, as when the console refuses a file that is not a video. */
export const UPLOAD_REFUSED_MESSAGE = 'The file was not accepted as a video';

/** The reason a file is refused before it is sent, by its name and type. */
export const NOT_A_VIDEO_MESSAGE = 'Not an MP4, MKV, MOV, or WebM video';

/** Whether the browser's file looks like a video the console takes; anything else fails at once without being sent. */
export const isVideoFile = (file: { name: string; type: string }): boolean =>
  file.type.startsWith('video/') || /\.(mp4|mkv|mov|webm)$/i.test(file.name);

/** Whether the browser is working on the file: sending or converting it, or queuing its job. */
export const isActive = (item: BatchItem): boolean =>
  item.status === 'uploading' ||
  item.status === 'converting' ||
  (item.jobId === null && (item.status === 'waiting' || item.status === 'analysing'));

export function newBatchItem(file: { name: string; size: number; type: string }, key: string): BatchItem {
  const item: BatchItem = {
    key,
    name: file.name,
    size: file.size,
    status: 'queued',
    failedAt: null,
    uploadProgress: 0,
    error: null,
    converted: null,
    report: null,
    jobId: null,
  };
  return isVideoFile(file) ? item : { ...item, status: 'failed', failedAt: 'uploading', error: NOT_A_VIDEO_MESSAGE };
}

/**
 * The file to start next: the first queued one, and only when the browser is
 * not uploading, converting or queuing another, so uploads run strictly one at
 * a time and in the order added. A file whose job is queued or analysing on
 * the server does not hold the next upload back.
 */
export function nextToRun(items: readonly BatchItem[]): string | null {
  if (items.some(isActive)) return null;
  return items.find((item) => item.status === 'queued')?.key ?? null;
}

/** Removes a file that has not started; a running or finished file stays. */
export function removeQueued(items: readonly BatchItem[], key: string): BatchItem[] {
  return items.filter((item) => item.key !== key || item.status !== 'queued');
}

/**
 * Cancels the batch: every file not uploaded yet is cancelled. The running file
 * is only cancelled while its bytes upload (the caller aborts it); a file being
 * converted finishes converting and is queued. Jobs still waiting on the server
 * are cancelled by the caller (waitingJobIds); an analysing one finishes.
 */
export function cancelQueued(items: readonly BatchItem[]): BatchItem[] {
  return items.map((item) => (item.status === 'queued' ? { ...item, status: 'cancelled' } : item));
}

/** The batch's jobs still queued on the server, which cancelling the batch cancels too. */
export const waitingJobIds = (items: readonly BatchItem[]): number[] =>
  items.flatMap((item) => (item.jobId !== null && item.status === 'waiting' ? [item.jobId] : []));

/** Whether Retry on the row re-sends the file itself: an upload or conversion that failed, before any job existed. */
export const canRetryUpload = (item: BatchItem): boolean =>
  item.status === 'failed' && item.jobId === null && item.failedAt !== 'analysing';

/** Puts a file whose upload failed back in the queue, as if just added. */
export function requeue(items: readonly BatchItem[], key: string): BatchItem[] {
  return items.map((item) =>
    item.key === key && canRetryUpload(item)
      ? { ...item, status: 'queued', failedAt: null, error: null, uploadProgress: 0, converted: null }
      : item,
  );
}

/** A job's state as a row's: queued is waiting, running is analysing; done carries the report link. */
export function jobState(job: AnalysisJob): Pick<BatchItem, 'status' | 'failedAt' | 'error' | 'report' | 'analysingSince'> {
  switch (job.status) {
    case 'queued':
      return { status: 'waiting', failedAt: null, error: null, report: null };
    case 'running':
      return { status: 'analysing', failedAt: null, error: null, report: null, analysingSince: job.startedAt };
    case 'done':
      return {
        status: 'done',
        failedAt: null,
        error: null,
        report: job.videoId && job.revisionId !== null ? { videoId: job.videoId, revisionId: job.revisionId } : null,
      };
    case 'failed':
      return { status: 'failed', failedAt: 'analysing', error: job.error, report: null };
    default:
      return { status: 'cancelled', failedAt: null, error: null, report: null };
  }
}

/** The rows with a job take its latest state from the polled list; a job missing from the list leaves its row as it was. */
export function applyJobs(items: readonly BatchItem[], jobs: readonly AnalysisJob[]): BatchItem[] {
  const byId = new Map(jobs.map((job) => [job.id, job]));
  return items.map((item) => {
    const job = item.jobId === null ? undefined : byId.get(item.jobId);
    return job ? { ...item, ...jobState(job) } : item;
  });
}

/** A row for a job from the server's list (another tab, or before this page was opened): size unknown, never re-sendable. */
export function jobItem(job: AnalysisJob): BatchItem {
  return {
    key: `job-${job.id}`,
    name: job.filename,
    size: null,
    uploadProgress: 100,
    converted: null,
    jobId: job.id,
    ...jobState(job),
  };
}

/** Whether cancelling now should abort the running file: only while its bytes are still uploading. */
export const shouldAbortRunning = (items: readonly BatchItem[]): boolean => items.some((item) => item.status === 'uploading');

const failureMessage = (error: unknown, fallback: string): string =>
  error instanceof Error && error.message.trim() ? error.message : fallback;

/**
 * Runs one file through Uploading and Converting, then queues its analysis
 * job, reporting each change through `update`; from then on the row follows
 * the job (applyJobs). It never throws: a failure is recorded on the item at
 * the step that failed, so the next file starts as usual.
 */
export async function runBatchItem(
  file: File,
  model: string,
  deps: BatchDeps,
  update: (change: Partial<BatchItem>) => void,
): Promise<void> {
  let stage: BatchStage = 'uploading';
  try {
    update({ status: stage, uploadProgress: 0, error: null, failedAt: null });
    const uploaded = await deps.upload(
      file,
      (uploadProgress) => update({ uploadProgress }),
      () => {
        stage = 'converting';
        update({ status: stage, uploadProgress: 100 });
      },
    );
    stage = 'analysing';
    update({ status: 'waiting', name: uploaded.filename, converted: uploaded.converted });
    const job = await deps.enqueue({ filePath: uploaded.filePath, filename: uploaded.filename, model });
    update({ jobId: job.id, ...jobState(job) });
  } catch (error) {
    if (stage !== 'analysing' && error instanceof Error && error.message === UPLOAD_CANCELLED_MESSAGE) {
      update({ status: 'cancelled', error: null });
      return;
    }
    const fallback = stage === 'analysing' ? 'The video could not be queued for analysis' : UPLOAD_REFUSED_MESSAGE;
    update({ status: 'failed', failedAt: stage, error: failureMessage(error, fallback) });
  }
}

/**
 * Runs the queue until no file is left to start: `current` reads the latest
 * items (files may be added, removed or cancelled meanwhile) and `run` takes
 * one file to the end. One failing file never stops the ones after it.
 */
export async function drainQueue(current: () => readonly BatchItem[], run: (key: string) => Promise<void>): Promise<void> {
  for (let key = nextToRun(current()); key !== null; key = nextToRun(current())) {
    try {
      await run(key);
    } catch {
      // runBatchItem records its own failure; the queue just moves on.
    }
  }
}

/** 0 to 100 for one file: the upload fills the first third as it goes, then a third per step. */
export function itemProgress(item: BatchItem): number {
  if (item.status === 'done') return 100;
  if (item.status === 'queued' || item.status === 'cancelled') return 0;
  if (item.status === 'waiting') return Math.round((2 / BATCH_STAGES.length) * 100);
  const stage = item.status === 'failed' ? (item.failedAt ?? 'uploading') : item.status;
  const index = BATCH_STAGES.indexOf(stage);
  const within = stage === 'uploading' ? item.uploadProgress / 100 : 0.5;
  return Math.max(2, Math.round(((index + within) / BATCH_STAGES.length) * 100));
}

export interface BatchSummary {
  total: number;
  done: number;
  failed: number;
  cancelled: number;
  /** Files still queued, uploading, or waiting for or in analysis. */
  pending: number;
  /** Percent of the batch done: each file's own progress averaged, a failed or cancelled file counted as finished. */
  percent: number;
  /** No file is queued or running. */
  settled: boolean;
}

export function summariseBatch(items: readonly BatchItem[]): BatchSummary {
  const count = (status: BatchStatus) => items.filter((item) => item.status === status).length;
  const done = count('done');
  const failed = count('failed');
  const cancelled = count('cancelled');
  const pending = items.length - done - failed - cancelled;
  const finished = (item: BatchItem) => item.status === 'failed' || item.status === 'cancelled';
  return {
    total: items.length,
    done,
    failed,
    cancelled,
    pending,
    percent: items.length
      ? Math.round(items.reduce((sum, item) => sum + (finished(item) ? 100 : itemProgress(item)), 0) / items.length)
      : 0,
    settled: items.length > 0 && pending === 0,
  };
}

/** The jobs list's jobs: the queued and running ones the server listed, except those already shown as this page's own rows. */
export function otherJobs(jobs: readonly AnalysisJob[], shown: Iterable<number | null>): AnalysisJob[] {
  const own = new Set([...shown].filter((id): id is number => id !== null));
  return jobs.filter((job) => (job.status === 'queued' || job.status === 'running') && !own.has(job.id));
}

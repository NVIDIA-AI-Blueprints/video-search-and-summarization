// SPDX-License-Identifier: Apache-2.0

// Browser-side calls for the face-blur toggle: switch it (which queues a re-blur job) and follow that job.
// Both throw an Error whose message is the route's own.

import { type AnalysisJob, isFinished } from '@/modules/jobs/domain/job';

export async function setBlurFaces(videoId: string, blurFaces: boolean): Promise<AnalysisJob | null> {
  const response = await fetch(`/api/videos/${encodeURIComponent(videoId)}/privacy`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ blurFaces }),
  });
  const payload = (await response.json().catch(() => ({}))) as { error?: string; privacy?: { job: AnalysisJob | null } };
  if (!response.ok || !payload.privacy) throw new Error(payload.error || 'Could not change the face blur setting');
  return payload.privacy.job;
}

async function listJobs(signal?: AbortSignal): Promise<AnalysisJob[]> {
  const response = await fetch('/api/jobs', { cache: 'no-store', signal });
  const payload = (await response.json().catch(() => ({}))) as { error?: string; jobs?: AnalysisJob[] };
  if (!response.ok || !payload.jobs) throw new Error(payload.error || 'Could not read the analysis jobs');
  return payload.jobs;
}

/** The job with this id from the queue's list; null when it is no longer listed. */
export async function fetchJob(jobId: number, signal?: AbortSignal): Promise<AnalysisJob | null> {
  return (await listJobs(signal)).find((job) => job.id === jobId) ?? null;
}

/** A re-blur job of the video that is still queued or running; null when there is none. */
export async function findPendingReanalysis(videoId: string, signal?: AbortSignal): Promise<AnalysisJob | null> {
  return (await listJobs(signal)).find((job) => job.reanalyseVideoId === videoId && !isFinished(job)) ?? null;
}

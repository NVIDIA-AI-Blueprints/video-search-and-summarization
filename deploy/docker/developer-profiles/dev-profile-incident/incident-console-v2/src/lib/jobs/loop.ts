// SPDX-License-Identifier: Apache-2.0

// The background analysis loop: runs inside the console's Node process,
// started once by src/instrumentation.ts. Every POLL_MS it runs one tick
// (modules/jobs/application runNextJob): claim the oldest queued job, analyse
// it with the same code as POST /api/analysis, store done or failed. A job queued
// by the privacy toggle (reanalyseVideoId set) does not analyse: in agent mode it
// re-blurs the video's evidence and keeps the report (reblurVideo, seconds, its own
// timeout); in gateway mode no images are stored, so it re-analyses as before. One job at
// a time per console, like the batch upload, which keeps within the model
// provider's rate limits. It is not a request, so no route maxDuration applies;
// each model's own timeout (contracts/inference.json) still bounds an analysis.
// ANALYSIS_JOB_LOOP=off leaves it stopped (jobs then wait for another console).

import { getAnalysisJobs, getUploadAnalysis } from '@/lib/container';
import { getServiceConfiguration, isSupabaseConfigured, requireAnalysisConfiguration } from '@/lib/env';
import { analyseUpload } from '@/modules/analysis/application/analyse-upload';
import { reblurVideo } from '@/modules/analysis/application/reblur-video';
import { jobVideoId, runNextJob } from '@/modules/jobs/application';

export const POLL_MS = 3000;

// One loop per process: Next's dev server can evaluate the instrumentation hook
// again (and modules are re-instantiated on reload), so the guard lives on
// globalThis. The atomic claim makes a second loop harmless anyway.
const STARTED = Symbol.for('incident-console-v2.analysis-job-loop');
type LoopGlobal = typeof globalThis & { [STARTED]?: boolean };

const log = (message: string) => console.warn(`[analysis jobs] ${message}`);

/** One tick; true when a job ran, so the loop takes the next one without waiting. */
export async function tick(): Promise<boolean> {
  const config = getServiceConfiguration();
  if (!isSupabaseConfigured(config)) return false;
  const finished = await runNextJob({
    jobs: getAnalysisJobs(config),
    analyse: (job) => {
      const deps = getUploadAnalysis(requireAnalysisConfiguration(config));
      if (job.reanalyseVideoId !== null && deps.mode === 'agent') {
        return reblurVideo({ videoId: job.reanalyseVideoId, r2Key: job.filePath, model: job.model, blurFaces: job.blurFaces }, deps);
      }
      return analyseUpload(
        {
          r2Key: job.filePath,
          model: job.model,
          videoId: jobVideoId(job),
          existingVideo: job.reanalyseVideoId !== null,
          blurFaces: job.blurFaces,
        },
        deps,
      );
    },
    every: (ms, fn) => {
      const timer = setInterval(fn, ms);
      return () => clearInterval(timer);
    },
    warn: log,
  });
  if (finished) log(`job ${finished.id} (${finished.filename}) ${finished.status}${finished.error ? `: ${finished.error}` : ''}`);
  return finished !== null;
}

export function startAnalysisJobLoop(): void {
  const scope = globalThis as LoopGlobal;
  if (scope[STARTED] || process.env.ANALYSIS_JOB_LOOP?.trim().toLowerCase() === 'off') return;
  scope[STARTED] = true;
  log(`loop started: one job at a time, polling every ${POLL_MS / 1000} s`);
  let lastError = '';
  const run = async () => {
    let busy = false;
    try {
      busy = await tick();
      lastError = '';
    } catch (error) {
      // The queue is unreachable (PostgREST down, table not migrated): say so once, keep polling.
      const message = error instanceof Error ? error.message : String(error);
      if (message !== lastError) log(`cannot reach the job queue: ${message}`);
      lastError = message;
    }
    setTimeout(run, busy ? 0 : POLL_MS);
  };
  setTimeout(run, POLL_MS);
}

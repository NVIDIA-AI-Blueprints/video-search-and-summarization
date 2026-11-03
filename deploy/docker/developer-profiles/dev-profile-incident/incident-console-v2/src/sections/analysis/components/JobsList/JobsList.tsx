// SPDX-License-Identifier: Apache-2.0

'use client';

import type { AnalysisJob } from '@/modules/jobs/domain/job';
import { jobItem } from '@/sections/analysis/batch-upload';
import { BatchFileRow } from '@/sections/analysis/components/BatchFileRow';

export interface JobsListProps {
  jobs: AnalysisJob[];
  error: string | null;
  onRetry(id: number): void;
  onCancel(id: number): void;
}

const jobId = (key: string): number => Number(key.replace(/^job-/, ''));

/**
 * The background analysis jobs not shown as this page's own rows: running and
 * queued ones; a finished job leaves the list. They come from the server, so they
 * survive a closed tab or a reload.
 */
export function JobsList({ jobs, error, onRetry, onCancel }: JobsListProps) {
  if (jobs.length === 0 && !error) return null;
  return (
    <section
      aria-labelledby="analysis-jobs-title"
      className="mt-6 flex flex-col gap-3 border-t border-border pt-5"
      data-testid="analysis-jobs"
    >
      <div className="flex flex-col gap-1">
        <h2 className="text-sm font-semibold text-foreground" id="analysis-jobs-title">
          Analysis jobs
        </h2>
        <p className="text-xs text-muted-foreground">
          Uploaded videos are analysed on the server, one at a time, and keep going when this tab is closed. Only running and queued jobs
          are listed here.
        </p>
      </div>
      {error && <p className="text-xs text-destructive">{error}</p>}
      <ul aria-label="Analysis jobs" className="flex flex-col gap-3">
        {jobs.map((job) => (
          <BatchFileRow
            item={jobItem(job)}
            key={job.id}
            onCancel={(key) => onCancel(jobId(key))}
            onRetry={job.status === 'failed' ? (key) => onRetry(jobId(key)) : undefined}
          />
        ))}
      </ul>
    </section>
  );
}

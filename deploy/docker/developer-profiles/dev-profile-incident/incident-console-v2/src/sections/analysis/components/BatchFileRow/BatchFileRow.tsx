// SPDX-License-Identifier: Apache-2.0

'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Check, Loader2, RotateCcw, Video, X } from 'lucide-react';
import { cn } from 'cn';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { type BatchItem, type BatchStage, type BatchStatus, batchStageLabels, itemProgress } from '@/sections/analysis/batch-upload';
import { formatElapsed } from '@/sections/analysis/stage-progress';

/** The steps a row shows: converting is part of the first, as in the analysis strip. */
const ROW_STEPS: Array<{ key: 'uploading' | 'analysing'; label: string }> = [
  { key: 'uploading', label: 'Uploading videos' },
  { key: 'analysing', label: 'Analysing' },
];
const rowStep = (stage: BatchStage): 'uploading' | 'analysing' => (stage === 'analysing' ? 'analysing' : 'uploading');

const statusLabel: Record<BatchStatus, string> = {
  queued: 'Queued',
  waiting: 'Queued',
  uploading: 'Uploading',
  converting: 'Uploading',
  analysing: 'Analysing',
  done: 'Done',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

function announcement(item: BatchItem): string {
  switch (item.status) {
    case 'failed':
      return `${item.name} stopped at ${stepLabel(item.failedAt)}: ${item.error ?? 'the step failed'}.`;
    case 'done':
      return `${item.name}: report ready.`;
    case 'queued':
      return `${item.name}: queued.`;
    case 'waiting':
      return `${item.name}: uploaded, queued for analysis.`;
    case 'cancelled':
      return item.jobId !== null ? `${item.name}: cancelled before its analysis started.` : `${item.name}: cancelled before it was stored.`;
    case 'uploading':
      return `${item.name}: uploading${item.uploadProgress > 0 ? ` (${item.uploadProgress}%)` : ''}.`;
    case 'converting':
      return `${item.name}: uploading, converting to H.264.`;
    default:
      return `${item.name}: ${batchStageLabels[item.status as BatchStage]}.`;
  }
}

const stepLabel = (stage: BatchStage | null): string => ROW_STEPS.find((step) => step.key === rowStep(stage ?? 'uploading'))?.label ?? '';

/** The message as a sentence, so what follows it reads on. */
const sentence = (text: string | null): string => (text && !/[.!?]$/.test(text) ? `${text}.` : (text ?? ''));

/**
 * Seconds the file has spent in its current step; restarts when the step changes and ticks only while it runs.
 * `since` (naive UTC from the server) dates a job's analysis from its real start, even on a reopened page.
 */
function useStepSeconds(status: BatchStatus, since: string | null = null): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    setSeconds(0);
    if (status !== 'converting' && status !== 'analysing') return;
    const fromServer = since ? Date.parse(since.endsWith('Z') ? since : `${since}Z`) : Number.NaN;
    const started = Number.isFinite(fromServer) ? Math.min(fromServer, Date.now()) : Date.now();
    const tick = () => setSeconds((Date.now() - started) / 1000);
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [status, since]);
  return seconds;
}

export interface BatchFileRowProps {
  item: BatchItem;
  /** Drops a file not uploaded yet; omitted for rows built from jobs. */
  onRemove?(key: string): void;
  /** Retry on a failed row (shown only when given). */
  onRetry?(key: string): void;
  /** Cancels a row whose job waits in the queue (shown only when given). */
  onCancel?(key: string): void;
}

export function BatchFileRow({ item, onRemove, onRetry, onCancel }: BatchFileRowProps) {
  const progress = itemProgress(item);
  // A waiting row has uploaded and converted; its analysis has not started.
  const reached = item.status === 'failed' ? (item.failedAt ?? 'uploading') : item.status === 'waiting' ? 'analysing' : item.status;
  const stageIndex = ROW_STEPS.findIndex((step) => step.key === (reached === 'converting' ? 'uploading' : reached));
  const stepSeconds = useStepSeconds(item.status, item.status === 'analysing' ? (item.analysingSince ?? null) : null);
  return (
    <li className="rounded-2xl border border-border bg-card p-3.5 sm:p-4" data-file={item.name} data-status={item.status}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
            <Video className="size-4" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <p className="break-all text-sm font-semibold">{item.name}</p>
            {item.size !== null && <p className="text-xs text-muted-foreground">{(item.size / 1024 / 1024).toFixed(1)} MB</p>}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Badge
            className={cn(item.status === 'failed' && 'border-destructive/30 bg-destructive/10 text-destructive')}
            variant={item.status === 'done' ? 'default' : 'secondary'}
          >
            {statusLabel[item.status]}
          </Badge>
          {item.status === 'failed' && onRetry && (
            <Button aria-label={`Retry ${item.name}`} onClick={() => onRetry(item.key)} size="sm" type="button" variant="outline">
              <RotateCcw data-icon="inline-start" />
              Retry
            </Button>
          )}
          {item.status === 'waiting' && item.jobId !== null && onCancel && (
            <Button
              aria-label={`Cancel the analysis of ${item.name}`}
              onClick={() => onCancel(item.key)}
              size="sm"
              type="button"
              variant="ghost"
            >
              <X data-icon="inline-start" />
              Cancel
            </Button>
          )}
          {item.status === 'queued' && onRemove && (
            <Button
              aria-label={`Remove ${item.name} from the queue`}
              onClick={() => onRemove(item.key)}
              size="sm"
              type="button"
              variant="ghost"
            >
              <X data-icon="inline-start" />
              Remove
            </Button>
          )}
        </div>
      </div>

      <div className="mt-3 flex items-center gap-3">
        <Progress
          aria-label={`Progress for ${item.name}`}
          aria-valuenow={progress}
          aria-valuetext={announcement(item)}
          className={cn('h-1.5 w-full', item.status === 'failed' && '[&>div]:bg-destructive')}
          value={progress}
        />
        <span className="font-mono text-xs font-semibold">{progress}%</span>
      </div>

      <ol className="mt-3 grid grid-cols-2 gap-1.5" aria-label={`Stages for ${item.name}`}>
        {ROW_STEPS.map(({ key: stage, label }, index) => {
          const done = item.status === 'done' || (stageIndex !== -1 && index < stageIndex);
          const failed = item.status === 'failed' && index === stageIndex;
          const active = item.status === stage || (stage === 'uploading' && item.status === 'converting');
          return (
            <li
              aria-current={active ? 'step' : undefined}
              className={cn(
                'flex flex-col gap-1 rounded-xl border p-2 text-[11px]',
                done && 'border-primary/30 bg-primary/10 text-primary',
                active && 'border-primary bg-primary/5 font-medium text-primary shadow-xs',
                failed && 'border-destructive/40 bg-destructive/10 text-destructive',
                !done && !active && !failed && 'border-border bg-card/40 text-muted-foreground',
              )}
              key={stage}
            >
              <span className="flex items-center font-mono text-[11px] font-semibold">
                {done ? <Check className="size-3" aria-hidden="true" /> : null}
                {failed ? <X className="size-3" aria-hidden="true" /> : null}
                {active ? <Loader2 className="size-3 animate-spin" aria-hidden="true" /> : null}
                {!done && !failed && !active ? String(index + 1).padStart(2, '0') : null}
                {done && <span className="sr-only">(completed)</span>}
                {active && <span className="sr-only">(current step)</span>}
                {failed && <span className="sr-only">(failed)</span>}
                {active && item.status === 'uploading' && item.uploadProgress > 0 && (
                  <span className="ml-auto text-primary/80">{item.uploadProgress}%</span>
                )}
                {active && (stage === 'analysing' || item.status === 'converting') && (
                  <span className="ml-auto text-primary/80">{formatElapsed(stepSeconds)}</span>
                )}
              </span>
              <span className="leading-snug">{label}</span>
            </li>
          );
        })}
      </ol>

      {item.status === 'failed' && (
        <Alert className="mt-3" variant="destructive">
          <AlertDescription>
            <strong className="font-semibold">Stopped at {stepLabel(item.failedAt)}.</strong> {sentence(item.error)}{' '}
            {onRetry ? 'Retry runs it again; the' : 'The'} other videos carry on.
          </AlertDescription>
        </Alert>
      )}

      {item.status === 'done' && !item.report && (
        <p className="mt-3 text-xs text-muted-foreground">Analysed; the video has since been deleted.</p>
      )}

      {item.status === 'done' && item.report && (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs">
          <span className="text-muted-foreground">
            {item.converted === null
              ? 'Analysed.'
              : item.converted
                ? 'Converted to H.264 MP4 and analysed.'
                : 'Stored as uploaded and analysed.'}
          </span>
          <Link
            className="font-semibold text-primary hover:underline"
            href={`/reports/${encodeURIComponent(item.report.videoId)}?revision=${item.report.revisionId}`}
          >
            Open report →
          </Link>
        </div>
      )}
    </li>
  );
}

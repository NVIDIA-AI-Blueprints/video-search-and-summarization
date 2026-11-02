// SPDX-License-Identifier: Apache-2.0

'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { Plus, Upload } from 'lucide-react';
import { cn } from 'cn';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { otherJobs } from '@/sections/analysis/batch-upload';
import { BatchFileRow } from '@/sections/analysis/components/BatchFileRow';
import { JobsList } from '@/sections/analysis/components/JobsList';
import { useAnalysisJobs } from '@/sections/analysis/hooks/use-analysis-jobs';
import { useBatchUpload } from '@/sections/analysis/hooks/use-batch-upload';
import { checkUploadName, type UploadNameReport, useVideoUpload } from '@/sections/analysis/hooks/use-video-upload';

import {
  conversionNote,
  formatElapsed,
  getStageAnnouncement,
  getStageProgress,
  type StageKey,
  stageLabels,
  stageOrder,
} from '@/sections/analysis/stage-progress';

export type { StageKey };
export { getStageAnnouncement, getStageProgress, stageLabels, stageOrder };

interface ModelOption {
  id: string;
  label: string;
}

/** The warning to show before an upload whose stored name differs from the file's own, or null. */
function nameWarning(original: string, report: UploadNameReport): { name: string; message: string } | null {
  const notes: string[] = [];
  if (report.unsafeChars.length > 0) notes.push(`unsafe characters (${report.unsafeChars.join(' ')}) were replaced with _`);
  if (report.truncated) notes.push('the name was too long and was shortened');
  if (report.exists) notes.push(`a file named ${report.existingName} is already stored, so nothing will be overwritten`);
  if (notes.length === 0) return null;
  return { name: report.name, message: `${original} will be stored as ${report.name}: ${notes.join('; ')}.` };
}

export interface AnalysisWorkspaceProps {
  initialStage?: StageKey;
  initialUploadProgress?: number;
  initialFileName?: string;
  initialError?: string | null;
}

export function AnalysisWorkspace({ initialStage, initialUploadProgress, initialFileName, initialError }: AnalysisWorkspaceProps = {}) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  // When the bytes finished uploading and the route started probing or converting the file.
  const convertingStartRef = useRef<number | null>(null);
  const [file, setFile] = useState<File | null>(() => {
    if (initialFileName) {
      return new File(['fixture'], initialFileName, { type: 'video/mp4' });
    }
    return null;
  });
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [stage, setStage] = useState<StageKey>(initialStage ?? 'idle');
  const [lastActiveStage, setLastActiveStage] = useState<StageKey>(
    initialStage && initialStage !== 'idle' && initialStage !== 'error' ? initialStage : 'idle',
  );
  const [uploadProgress, setUploadProgress] = useState(initialUploadProgress ?? 0);
  const [error, setError] = useState<string | null>(initialError ?? null);
  const [nameNotice, setNameNotice] = useState<string | null>(null);
  const [storedName, setStoredName] = useState<string | null>(null);
  const [convertingSeconds, setConvertingSeconds] = useState(0);
  const [conversion, setConversion] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [model, setModel] = useState('');
  const { upload } = useVideoUpload();
  const jobs = useAnalysisJobs();
  const batch = useBatchUpload(jobs);
  // The single upload's background job, watched until it finishes.
  const [watchedJobId, setWatchedJobId] = useState<number | null>(null);

  function changeStage(newStage: StageKey) {
    setStage(newStage);
    if (newStage !== 'idle' && newStage !== 'error') {
      setLastActiveStage(newStage);
    }
  }

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const params = new URLSearchParams(window.location.search);
    const fixtureStage = params.get('fixtureStage') as StageKey | null;
    if (fixtureStage && fixtureStage in stageOrder) {
      const mockFileName = params.get('fixtureFile') || 'Assault018_x264_demo.mp4';
      const mockProgress = Number(params.get('fixtureProgress') || (fixtureStage === 'uploading' ? 45 : 0));
      const mockError = params.get('fixtureError') || (fixtureStage === 'error' ? 'Analysis request timed out.' : null);

      setFile(new File(['fixture-content'], mockFileName, { type: 'video/mp4' }));
      setStage(fixtureStage);
      if (fixtureStage !== 'idle' && fixtureStage !== 'error') {
        setLastActiveStage(fixtureStage);
      } else if (fixtureStage === 'error') {
        const failedAt = (params.get('fixtureFailedAt') as StageKey) || 'analyzing';
        setLastActiveStage(failedAt);
      }
      setUploadProgress(mockProgress);
      if (mockError) setError(mockError);
    }
  }, []);

  useEffect(() => {
    let active = true;
    void fetch('/api/models', { cache: 'no-store' })
      .then((response) => response.json() as Promise<{ models?: ModelOption[]; defaultModel?: string }>)
      .then((payload) => {
        if (!active) return;
        setModels(payload.models || []);
        setModel(payload.defaultModel || payload.models?.[0]?.id || '');
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  // The single upload follows its job: done opens the report, failed or cancelled stops with the reason.
  // biome-ignore lint/correctness/useExhaustiveDependencies: router is stable; only a new jobs list re-runs this
  useEffect(() => {
    if (watchedJobId === null) return;
    const job = jobs.jobs.find((entry) => entry.id === watchedJobId);
    if (!job) return;
    if (job.status === 'done' && job.videoId && job.revisionId !== null) {
      changeStage('saving');
      changeStage('complete');
      setWatchedJobId(null);
      router.push(`/reports/${encodeURIComponent(job.videoId)}?revision=${job.revisionId}`);
    } else if (job.status === 'failed' || job.status === 'cancelled') {
      setLastActiveStage('analyzing');
      setError(job.status === 'failed' ? job.error || 'The analysis failed' : 'Analysis cancelled.');
      setStage('error');
    } else if (stage === 'error') {
      changeStage('analyzing');
      setError(null);
    }
  }, [jobs.jobs, watchedJobId]);

  // The Converting step shows how long the route has been working on the file.
  useEffect(() => {
    if (stage !== 'converting') return;
    const started = convertingStartRef.current ?? Date.now();
    const tick = () => setConvertingSeconds((Date.now() - started) / 1000);
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [stage]);

  useEffect(() => {
    if (!file) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  // A newly selected file starts the one-shot pipeline immediately, unless its name needs a warning first.
  // biome-ignore lint/correctness/useExhaustiveDependencies: only a new file re-runs the check
  useEffect(() => {
    if (!file || stage !== 'idle') return;
    if (typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('fixtureStage')) return;
    let active = true;
    void checkUploadName(file.name)
      .then((report) => (active ? nameWarning(file.name, report) : null))
      .catch(() => null)
      .then((warning) => {
        if (!active) return;
        if (warning) {
          setNameNotice(warning.message);
          setStoredName(warning.name);
        } else void analyze();
      });
    return () => {
      active = false;
    };
  }, [file]);

  function chooseFile(selected: File | null) {
    if (!selected) return;
    if (!selected.type.startsWith('video/') && !/\.(mp4|mkv|mov|webm)$/i.test(selected.name)) {
      setError('Choose an MP4, MKV, MOV, or WebM video.');
      return;
    }
    setFile(selected);
    setWatchedJobId(null);
    setNameNotice(null);
    setStoredName(null);
    setError(null);
    setConversion(null);
    setStage('idle');
    setLastActiveStage('idle');
    setUploadProgress(0);
  }

  /** One file on an empty page runs as a single upload; several, or any added to a batch, are queued. */
  function chooseFiles(selected: File[]) {
    if (selected.length === 0) return;
    if (batch.items.length === 0 && selected.length === 1) {
      chooseFile(selected[0]);
      return;
    }
    // A finished or failed single upload gives way to the batch.
    setFile(null);
    setWatchedJobId(null);
    setStage('idle');
    setNameNotice(null);
    setError(null);
    batch.add(selected, model);
  }

  async function analyze() {
    if (!file) return;
    // A failed analysis is retried on the server: the file is already uploaded.
    if (watchedJobId !== null) {
      setError(null);
      changeStage('analyzing');
      await jobs.retry(watchedJobId);
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    setError(null);
    setNameNotice(null);
    setConversion(null);
    setUploadProgress(0);
    setConvertingSeconds(0);
    convertingStartRef.current = null;

    try {
      changeStage('uploading');
      const { filePath, filename, converted } = await upload(file, controller.signal, setUploadProgress, () => {
        convertingStartRef.current = Date.now();
        changeStage('converting');
      });
      const started = convertingStartRef.current;
      setConversion(conversionNote(converted, started ? (Date.now() - started) / 1000 : 0));

      changeStage('analyzing');
      // The analysis runs as a background job; the effect above opens the report when it is done.
      const job = await jobs.enqueue({ filePath, filename, model });
      setWatchedJobId(job.id);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : 'Analysis failed';
      setError(message === 'Upload was cancelled' ? 'Analysis cancelled.' : message);
      setStage('error');
    } finally {
      abortRef.current = null;
    }
  }

  const activeIndex = stageOrder[stage];
  const effectiveActiveIndex = stage === 'error' ? stageOrder[lastActiveStage] : activeIndex;
  const failedStageIndex = stage === 'error' ? stageOrder[lastActiveStage] : -1;
  const busy = ['uploading', 'converting', 'analyzing', 'saving'].includes(stage);
  // Once the job is queued nothing in this tab is needed: the server analyses it.
  const queuedOnServer = stage === 'analyzing' && watchedJobId !== null;
  const listedJobs = otherJobs(jobs.jobs, [...batch.items.map((item) => item.jobId), watchedJobId]);
  const progressValue = getStageProgress(stage, uploadProgress, lastActiveStage);
  const announcement = getStageAnnouncement(stage, uploadProgress, error, convertingSeconds);
  const { summary } = batch;
  const batchHeadline = [
    `Batch of ${summary.total} ${summary.total === 1 ? 'video' : 'videos'}: ${summary.done} done`,
    summary.pending > 0 ? `${summary.pending} to go` : '',
    summary.failed > 0 ? `${summary.failed} failed` : '',
    summary.cancelled > 0 ? `${summary.cancelled} cancelled` : '',
  ]
    .filter(Boolean)
    .join(', ');

  return (
    <Card
      className="relative rounded-4xl border border-border bg-white/80 p-4 shadow-panel backdrop-blur-sm md:p-6 ring-0 flex flex-col gap-0"
      aria-label="Video analysis workspace"
    >
      <input
        accept="video/mp4,video/x-matroska,video/quicktime,video/webm,.mp4,.mkv,.mov,.webm"
        className="sr-only"
        multiple
        onChange={(event) => {
          chooseFiles(Array.from(event.target.files ?? []));
          event.target.value = '';
        }}
        ref={inputRef}
        type="file"
      />

      {models.length > 0 && (
        <Card className="mb-4 flex flex-col gap-2 rounded-2xl border border-border bg-card px-4 py-3 ring-0 shadow-none sm:flex-row sm:flex-wrap sm:items-center sm:justify-between sm:gap-x-4">
          {/* The select keeps its natural width (the longest model name) and never shrinks; the note wraps below it instead. */}
          <label
            className="flex flex-col gap-1 text-xs font-bold uppercase tracking-[0.12em] text-ink/45 sm:shrink-0 sm:flex-row sm:items-center sm:gap-3"
            htmlFor="analysis-model"
          >
            <span className="whitespace-nowrap">Vision-language model</span>
            <Select disabled={busy || batch.summary.pending > 0} onValueChange={(value) => setModel(value)} value={model}>
              <SelectTrigger className="normal-case tracking-normal sm:w-auto sm:min-w-[19rem] sm:shrink-0" id="analysis-model">
                <SelectValue placeholder="Choose model" />
              </SelectTrigger>
              <SelectContent>
                {models.map((option) => (
                  <SelectItem key={option.id} value={option.id}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
        </Card>
      )}

      {batch.items.length > 0 ? (
        <div className="flex flex-col gap-4" data-testid="batch">
          <div className="flex flex-col gap-2.5">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span className="font-medium text-foreground">{batchHeadline}</span>
              <span className="font-mono font-semibold text-foreground">{batch.summary.percent}%</span>
            </div>
            <Progress
              aria-label="Batch progress"
              aria-valuenow={batch.summary.percent}
              aria-valuetext={batchHeadline}
              className="h-2 w-full"
              value={batch.summary.percent}
            />
            <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
              {batchHeadline}
            </div>
          </div>
          <ul className="flex flex-col gap-3" aria-label="Videos in this batch">
            {batch.items.map((item) => (
              <BatchFileRow
                item={item}
                key={item.key}
                onCancel={(key) => {
                  const id = batch.items.find((entry) => entry.key === key)?.jobId;
                  if (id) void jobs.cancel(id);
                }}
                onRemove={batch.remove}
                onRetry={batch.retry}
              />
            ))}
          </ul>
          <div className="flex flex-wrap gap-3">
            {batch.summary.settled ? (
              <>
                <Button asChild className="h-11 flex-1 rounded-full text-sm font-semibold" size="lg">
                  <Link href="/library">Go to library</Link>
                </Button>
                <Button className="h-11 rounded-full text-sm font-semibold" onClick={batch.clear} size="lg" type="button" variant="outline">
                  Start a new batch
                </Button>
              </>
            ) : (
              <>
                <Button className="h-11 flex-1 rounded-full text-sm font-semibold" disabled size="lg" type="button">
                  Working on {batch.summary.pending} {batch.summary.pending === 1 ? 'video' : 'videos'}, one at a time…
                </Button>
                <Button
                  className="h-11 rounded-full text-sm font-semibold"
                  onClick={batch.cancel}
                  size="lg"
                  type="button"
                  variant="outline"
                >
                  Cancel batch
                </Button>
              </>
            )}
            <Button
              className="h-11 rounded-full text-sm font-semibold"
              onClick={() => inputRef.current?.click()}
              size="lg"
              type="button"
              variant="outline"
            >
              <Plus data-icon="inline-start" />
              Add videos
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Each video becomes its own report. Videos upload one at a time from this tab, then wait for analysis on the server: once a video
            shows Queued after its upload, closing this tab no longer stops it; a video still uploading is lost if the tab closes.
            Cancelling drops the videos not yet analysing; one already analysing finishes.
          </p>
        </div>
      ) : file && previewUrl ? (
        <div className="overflow-hidden rounded-[1.45rem] bg-ink text-white">
          <div className="relative aspect-video w-full min-w-0 min-h-0 overflow-hidden bg-black flex items-center justify-center">
            <video className="max-h-full max-w-full object-contain" controls preload="metadata" src={previewUrl}>
              <track kind="captions" />
            </video>
          </div>
          <div className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <p className="truncate font-semibold">{file.name}</p>
              <p className="mt-1 text-xs text-white/55">{(file.size / 1024 / 1024).toFixed(1)} MB · ready for analysis</p>
            </div>
            {!busy && (
              <Button
                variant="ghost"
                size="sm"
                className="text-signal hover:text-signal/80 hover:bg-white/10 focus-visible:ring-2 focus-visible:ring-ring"
                onClick={() => inputRef.current?.click()}
                type="button"
              >
                Replace video
              </Button>
            )}
          </div>
        </div>
      ) : (
        <Card
          className={cn(
            'relative flex flex-col items-center justify-center rounded-3xl border-2 border-dashed py-14 px-6 text-center transition-colors cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:py-20 sm:px-10',
            dragging ? 'border-primary bg-primary/10' : 'border-border bg-muted/30 hover:border-primary/50 hover:bg-muted/50',
          )}
          role="button"
          tabIndex={0}
          aria-labelledby="upload-title"
          aria-describedby="upload-description"
          onClick={() => inputRef.current?.click()}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              inputRef.current?.click();
            }
          }}
          onDragEnter={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={(event) => {
            event.preventDefault();
            if (event.currentTarget.contains(event.relatedTarget as Node)) return;
            setDragging(false);
          }}
          onDragOver={(event) => {
            event.preventDefault();
            event.dataTransfer.dropEffect = 'copy';
          }}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            chooseFiles(Array.from(event.dataTransfer.files));
          }}
        >
          <CardHeader className="flex w-full flex-col items-center gap-3 p-0">
            <div className="upload-orbit mx-auto flex size-16 items-center justify-center rounded-full border border-primary/20 bg-background text-primary shadow-lg shadow-primary/10">
              <Upload className="size-8" aria-hidden="true" />
            </div>
            <Badge variant="secondary" className="font-mono text-[11px] font-semibold uppercase tracking-[0.2em] text-primary">
              New analysis
            </Badge>
            <CardTitle className="text-2xl font-semibold tracking-tight text-foreground" id="upload-title">
              Drop videos here
            </CardTitle>
            <CardDescription className="max-w-sm text-sm leading-6 text-muted-foreground" id="upload-description">
              MP4, MKV, MOV, or WebM. Analysis starts automatically after selection, uses the model above and can take several minutes.
              Several videos run one after another, each to its own report.
            </CardDescription>
          </CardHeader>
          <CardContent className="mt-6 flex flex-col items-center p-0">
            <Button variant="outline" size="sm" className="pointer-events-none rounded-full" tabIndex={-1} type="button">
              <Upload data-icon="inline-start" />
              Browse video files
            </Button>
          </CardContent>
        </Card>
      )}

      {error && !file && batch.items.length === 0 && (
        <Alert variant="destructive" className="mt-4">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {file && (
        <div className="mt-5 flex flex-col gap-4">
          {stage !== 'idle' && (
            <div className="flex flex-col gap-2.5" data-testid="analysis-progress">
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span className="font-medium text-foreground">
                  {stage === 'complete' ? 'Analysis complete' : stage === 'error' ? 'Analysis stopped' : 'Analysis in progress'}
                </span>
                <span className="font-mono font-semibold text-foreground">{progressValue}%</span>
              </div>

              <Progress
                className="h-2 w-full"
                value={progressValue}
                aria-label="Analysis progress"
                aria-valuenow={progressValue}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuetext={announcement}
              />

              <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
                {announcement}
              </div>

              <ol className="mt-1 grid gap-2 sm:grid-cols-4" aria-label="Analysis stages">
                {stageLabels.map((item, index) => {
                  const done = stage === 'complete' || (effectiveActiveIndex !== -1 && index < effectiveActiveIndex);
                  const active = index === effectiveActiveIndex && stage !== 'error';
                  const isFailed = stage === 'error' && index === failedStageIndex;
                  return (
                    <li
                      className={cn(
                        'flex flex-col gap-1.5 rounded-xl border p-3 text-xs transition-colors',
                        done && 'border-primary/30 bg-primary/10 text-primary',
                        active && 'border-primary bg-primary/5 text-primary font-medium shadow-xs',
                        !done && !active && !isFailed && 'border-border text-muted-foreground bg-card/40',
                        isFailed && 'border-destructive/40 bg-destructive/10 text-destructive',
                      )}
                      key={item.key}
                      aria-current={active ? 'step' : undefined}
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-mono text-[11px] font-semibold">
                          {done ? '✓' : isFailed ? '✕' : String(index + 1).padStart(2, '0')}
                        </span>
                        {done && <span className="sr-only">(completed)</span>}
                        {active && <span className="sr-only">(current step)</span>}
                        {isFailed && <span className="sr-only">(failed)</span>}
                        {active && item.key === 'uploading' && uploadProgress > 0 && (
                          <span className="font-mono text-[11px] text-primary/80">{uploadProgress}%</span>
                        )}
                        {active && stage === 'converting' && (
                          <span className="font-mono text-[11px] text-primary/80" data-testid="converting-elapsed">
                            {formatElapsed(convertingSeconds)}
                          </span>
                        )}
                      </div>
                      <span className="leading-snug">{item.label}</span>
                    </li>
                  );
                })}
              </ol>
              {conversion && <p className="text-xs text-muted-foreground">{conversion}</p>}
              {queuedOnServer && (
                <p className="text-xs text-muted-foreground">
                  The video is uploaded and its analysis runs on the server: closing this tab does not stop it, and it stays listed under
                  Analysis jobs.
                </p>
              )}
            </div>
          )}

          {nameNotice && stage === 'idle' && (
            <Alert className="border-amber-500/30 bg-amber-500/10 text-amber-900">
              <AlertDescription>{nameNotice}</AlertDescription>
            </Alert>
          )}
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          <div className="flex gap-3">
            <Button size="lg" className="flex-1 rounded-full text-sm font-semibold" disabled={busy} onClick={analyze} type="button">
              {busy
                ? stageLabels[stageOrder[stage]]?.label
                : stage === 'error'
                  ? 'Retry analysis'
                  : nameNotice && storedName
                    ? `Continue as ${storedName}`
                    : 'Analyze video'}
            </Button>
            {busy && !queuedOnServer && (
              <Button
                variant="outline"
                size="lg"
                className="rounded-full text-sm font-semibold"
                onClick={() => abortRef.current?.abort()}
                type="button"
              >
                Cancel
              </Button>
            )}
          </div>
        </div>
      )}

      <JobsList error={jobs.error} jobs={listedJobs} onCancel={(id) => void jobs.cancel(id)} onRetry={(id) => void jobs.retry(id)} />
    </Card>
  );
}

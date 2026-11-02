// SPDX-License-Identifier: Apache-2.0

'use client';

import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { fetchJob, findPendingReanalysis, setBlurFaces } from '@/lib/reports/privacy-client';
import { isFinished } from '@/modules/jobs/domain/job';
import { privacyJobLabel } from '@/modules/reports/domain/privacy-blur';

/** How often the pending re-blur is checked. */
const POLL_MS = 4000;

/**
 * The video's face-blur toggle. Switching it (after a confirmation) stores the setting and queues a re-blur: the same
 * report with its evidence images made again, blurred or not. The control then follows that job and, when it is done,
 * hands its new revision to onAnalysed so the page shows it. Earlier versions keep their own images. A job already
 * queued for the video is picked up on load.
 */
export function PrivacyControls({
  videoId,
  blurFaces,
  onAnalysed,
}: {
  videoId: string;
  blurFaces: boolean;
  onAnalysed: (revisionId: number | null) => void;
}) {
  const [checked, setChecked] = useState(blurFaces);
  const [confirm, setConfirm] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [jobId, setJobId] = useState<number | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => setChecked(blurFaces), [blurFaces]);

  // A re-blur of this video that is still queued or running (switched before a reload) is followed as well.
  useEffect(() => {
    const controller = new AbortController();
    findPendingReanalysis(videoId, controller.signal)
      .then((job) => {
        if (job) setJobId((current) => current ?? job.id);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [videoId]);

  useEffect(() => {
    if (jobId === null) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const check = async () => {
      try {
        const job = await fetchJob(jobId, controller.signal);
        if (!job) {
          setJobId(null);
          setStatus(null);
          return;
        }
        setStatus(privacyJobLabel(job));
        if (job.status === 'done') {
          setJobId(null);
          setStatus(null);
          onAnalysed(job.revisionId);
          return;
        }
        if (isFinished(job)) {
          setJobId(null);
          setStatus(null);
          setErrorMessage(`The face blur did not finish: ${job.error ?? job.status}. Switch the setting off and on to try again.`);
          return;
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') return;
      }
      timer = setTimeout(check, POLL_MS);
    };
    void check();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [jobId, onAnalysed]);

  async function apply(next: boolean) {
    setConfirm(null);
    setBusy(true);
    try {
      const job = await setBlurFaces(videoId, next);
      setChecked(next);
      if (job) {
        setJobId(job.id);
        setStatus(privacyJobLabel(job));
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Could not change the face blur setting');
    } finally {
      setBusy(false);
    }
  }

  const pending = busy || jobId !== null;
  return (
    <div className="flex items-center gap-2" data-testid="privacy-controls">
      <Switch
        aria-describedby={status ? 'privacy-status' : undefined}
        checked={checked}
        data-testid="privacy-toggle"
        disabled={pending}
        id="privacy-toggle"
        onCheckedChange={(next) => setConfirm(next)}
      />
      <Label className="text-xs font-semibold" htmlFor="privacy-toggle">
        Blur faces
      </Label>
      {pending && (
        <span className="flex items-center gap-1 text-xs text-muted-foreground" data-testid="privacy-status" id="privacy-status">
          <Loader2 aria-hidden="true" className="size-3 animate-spin" />
          {status ?? 'Saving…'}
        </span>
      )}

      <AlertDialog onOpenChange={(open) => !open && setConfirm(null)} open={confirm !== null}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirm ? 'Blur faces in this video?' : 'Stop blurring faces?'}</AlertDialogTitle>
            <AlertDialogDescription>
              {confirm
                ? 'The report is kept as it is. Its evidence frames, crops and clips are made again from the video with the heads of the people blurred, as a new version that this page then shows. Earlier versions keep their own images.'
                : 'The report is kept as it is. Its evidence frames, crops and clips are made again from the video without blurring, so they show faces, as a new version that this page then shows. Earlier blurred versions stay as they are.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction data-testid="privacy-confirm" onClick={() => confirm !== null && void apply(confirm)}>
              {confirm ? 'Blur faces' : 'Stop blurring'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog onOpenChange={(open) => !open && setErrorMessage(null)} open={errorMessage !== null}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Face blur</AlertDialogTitle>
            <AlertDialogDescription>{errorMessage}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogAction onClick={() => setErrorMessage(null)}>OK</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

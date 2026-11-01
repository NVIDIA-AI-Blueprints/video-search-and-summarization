// SPDX-License-Identifier: Apache-2.0
'use client';

import Link from 'next/link';
import { PictureInPicture2Icon } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { type CSSProperties, useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
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
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { chatTarget } from '@/lib/chat/report-chat';
import { uploadEvidenceImage } from '@/lib/evidence/evidence-image-upload';
import { draftChangeCount } from '@/lib/reports/compare';
import {
  DEFAULT_REPORT_BAND_HEIGHT,
  DEFAULT_TWO_PANE_BAND_WIDTHS,
  MAX_REPORT_BAND_HEIGHT,
  MIN_REPORT_BAND_HEIGHT,
  nudgeReportBandHeight,
  resizeReportBandHeight,
} from '@/lib/reports/report-band-layout';
import {
  draftFromView,
  draftProblems,
  editFromDraft,
  pendingImages,
  withUploadedImages,
  type EditDraft,
} from '@/modules/reports/domain/edit-draft';
import { reportHeading, type ReportView } from '@/modules/reports/domain/report-view';
import type { RevisionSummary } from '@/modules/reports/domain/revision';
import { trackedReportItems } from '@/modules/reports/domain/track';
import { ReanalysisDialog } from '@/sections/analysis/components/ReanalysisCard';
import { PrivacyControls } from '@/sections/reports/components/PrivacyControls';
import { ReportChat } from '@/sections/reports/components/ReportChat';
import { ReportEditPane } from '@/sections/reports/components/ReportEditPane';
import { ReportPane } from '@/sections/reports/components/ReportPane';
import { ReportVersions } from '@/sections/reports/components/ReportVersions';
import { TrackOverlay } from '@/sections/reports/components/TrackOverlay';

type LoadState = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; report: ReportView };

type Mode = 'view' | 'edit';
interface EditBase {
  revisionId: number;
  revisionNo: number;
  report: ReportView;
}

// Human revisions still record who saved them; the mockup removed names from the UI, so the console sends a neutral one.
const SAVE_AUTHOR = 'Reviewer';

const DEFAULT_VIDEO_ASPECT = 16 / 9;

/** revisionId null opens the incident's official version, else its newest. startSeconds, when set, is where the player seeks once it has loaded. */
export function ReportPage({
  videoId,
  revisionId,
  startSeconds = null,
}: {
  videoId: string;
  revisionId: number | null;
  startSeconds?: number | null;
}) {
  const router = useRouter();
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const [revisions, setRevisions] = useState<RevisionSummary[] | null>(null);
  const [revisionsError, setRevisionsError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>('view');
  const [_currentTime, setCurrentTime] = useState(0);
  // The player's own length, a second guard (besides the recorded length) before jumping to a time.
  const [playerDuration, setPlayerDuration] = useState<number | null>(null);
  // The stage is sized to the clip's own shape so a 4:3 clip is not letterboxed inside a 16:9 box.
  const [videoAspect, setVideoAspect] = useState(DEFAULT_VIDEO_ASPECT);
  const [pipSupported, setPipSupported] = useState(false);
  const [inPip, setInPip] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const startedAt = useRef(false);

  const [chatOpen, setChatOpen] = useState(false);
  const [draft, setDraft] = useState<EditDraft | null>(null);
  const [undoStack, setUndoStack] = useState<EditDraft[]>([]);
  const [editBase, setEditBase] = useState<EditBase | null>(null);
  const [compareRevisionId, setCompareRevisionId] = useState<number | null>(null);
  const [compareReport, setCompareReport] = useState<ReportView | null>(null);
  const [compareError, setCompareError] = useState<string | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [revertConfirmOpen, setRevertConfirmOpen] = useState(false);
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveProblems, setSaveProblems] = useState<string[]>([]);
  const [bandHeight, setBandHeight] = useState(DEFAULT_REPORT_BAND_HEIGHT);
  const reportBandRef = useRef<HTMLDivElement>(null);
  const resizingHeightRef = useRef(false);

  // biome-ignore lint/correctness/useExhaustiveDependencies: attempt is an explicit refetch trigger
  useEffect(() => {
    const controller = new AbortController();
    setState((previous) => (previous.status === 'ready' ? previous : { status: 'loading' }));
    fetch(`/api/reports/${encodeURIComponent(videoId)}${revisionId === null ? '' : `?revision=${revisionId}`}`, {
      cache: 'no-store',
      signal: controller.signal,
    })
      .then(async (response) => {
        const payload = (await response.json()) as { report?: ReportView; error?: string };
        if (!response.ok || !payload.report) throw new Error(payload.error || `Report request failed with HTTP ${response.status}`);
        setState({ status: 'ready', report: payload.report });
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        setState({ status: 'error', message: error instanceof Error ? error.message : 'Could not load the report' });
      });
    return () => controller.abort();
  }, [attempt, revisionId, videoId]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: attempt is an explicit refetch trigger
  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/reports/${encodeURIComponent(videoId)}/revisions`, { cache: 'no-store', signal: controller.signal })
      .then(async (response) => {
        const payload = (await response.json()) as { revisions?: RevisionSummary[]; error?: string };
        if (!response.ok || !payload.revisions) throw new Error(payload.error || `Versions request failed with HTTP ${response.status}`);
        setRevisions(payload.revisions);
        setRevisionsError(null);
      })
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return;
        setRevisionsError(cause instanceof Error ? cause.message : 'Could not load the versions');
      });
    return () => controller.abort();
  }, [videoId, attempt]);

  // A is any saved version; the draft's own version is already loaded, so it needs no request.
  useEffect(() => {
    if (mode !== 'edit' || !editBase || compareRevisionId === null) return;
    if (compareRevisionId === editBase.revisionId) {
      setCompareReport(editBase.report);
      setCompareError(null);
      return;
    }
    const controller = new AbortController();
    setCompareError(null);
    fetch(`/api/reports/${encodeURIComponent(videoId)}?revision=${compareRevisionId}`, { cache: 'no-store', signal: controller.signal })
      .then(async (response) => {
        const payload = (await response.json()) as { report?: ReportView; error?: string };
        if (!response.ok || !payload.report) throw new Error(payload.error || `Version request failed with HTTP ${response.status}`);
        setCompareReport(payload.report);
      })
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return;
        setCompareError(cause instanceof Error ? cause.message : 'Could not load the comparison version');
        setCompareReport(null);
      });
    return () => controller.abort();
  }, [compareRevisionId, editBase, mode, videoId]);

  // The browser's own Picture-in-Picture window floats over other tabs and apps, like YouTube's miniplayer.
  const ready = state.status === 'ready';

  // Tracked people: a box that follows each one in the player, selected from its evidence card. A new version starts unselected.
  const [trackSelection, setTrackSelection] = useState<{ revisionId: number; id: string } | null>(null);
  const shownReport = state.status === 'ready' ? state.report : null;
  const trackedItems = useMemo(() => (shownReport ? trackedReportItems(shownReport.structured) : []), [shownReport]);
  const selectedTrackId = trackSelection && trackSelection.revisionId === shownReport?.revisionId ? trackSelection.id : null;
  const selectTrack = useCallback(
    (id: string | null) => setTrackSelection(id && shownReport ? { revisionId: shownReport.revisionId, id } : null),
    [shownReport],
  );
  useEffect(() => {
    const video = videoRef.current;
    if (!ready || !video) return;
    setPipSupported(document.pictureInPictureEnabled === true && !video.disablePictureInPicture);
    const enter = () => setInPip(true);
    const leave = () => setInPip(false);
    video.addEventListener('enterpictureinpicture', enter);
    video.addEventListener('leavepictureinpicture', leave);
    return () => {
      video.removeEventListener('enterpictureinpicture', enter);
      video.removeEventListener('leavepictureinpicture', leave);
    };
  }, [ready]);

  const togglePip = useCallback(async () => {
    const video = videoRef.current;
    if (!video) return;
    try {
      if (document.pictureInPictureElement === video) await document.exitPictureInPicture();
      else await video.requestPictureInPicture();
    } catch {
      // The browser refused (clip not loaded yet, or another window already holds Picture-in-Picture): the inline player stays as it is.
    }
  }, []);

  const seek = useCallback((seconds: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = seconds;
    void video.play().catch(() => undefined);
  }, []);

  const startEdit = useCallback((base: ReportView) => {
    setDraft(draftFromView(base));
    setUndoStack([]);
    setEditBase({ revisionId: base.revisionId, revisionNo: base.revision.revisionNo, report: base });
    setCompareRevisionId(base.revisionId);
    setCompareReport(base);
    setCompareError(null);
    setMode('edit');
    setSaveOpen(false);
    setSaveProblems([]);
    setNote('');
  }, []);

  // The face-blur toggle's re-analysis finished: its new version replaces what the page shows.
  const showAnalysedVersion = useCallback(
    (id: number | null) => {
      setAttempt((value) => value + 1);
      if (id !== null) router.push(`/reports/${encodeURIComponent(videoId)}?revision=${id}`);
    },
    [router, videoId],
  );

  const openVersion = (id: number) => {
    setMode('view');
    setDraft(null);
    setUndoStack([]);
    setEditBase(null);
    setSaveOpen(false);
    router.push(`/reports/${encodeURIComponent(videoId)}?revision=${id}`);
  };

  if (state.status === 'loading') {
    return (
      <div className="pb-16" role="status">
        <div className="overflow-hidden border-y border-border bg-card shadow-panel">
          <div className="h-12 border-b border-border bg-muted/40" />
          <div className="grid gap-px bg-border lg:grid-cols-[1fr_22rem]">
            <Skeleton className="aspect-video w-full rounded-none" />
            <div className="space-y-3 p-4 bg-card">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
            </div>
          </div>
          <div className="space-y-3 p-8">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-5/6" />
            <Skeleton className="h-4 w-3/4" />
          </div>
          <span className="sr-only">Loading incident report</span>
        </div>
      </div>
    );
  }

  if (state.status === 'error') {
    return (
      <div className="mx-auto grid min-h-[65vh] max-w-xl place-items-center px-6 text-center">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.2em] text-destructive">Report unavailable</p>
          <h1 className="mt-3 text-3xl font-semibold">We couldn&apos;t reopen this analysis.</h1>
          <p className="mt-4 text-sm leading-6 text-muted-foreground">{state.message}</p>
          <div className="mt-7 flex justify-center gap-3">
            <Button onClick={() => setAttempt((value) => value + 1)} type="button">
              Try again
            </Button>
            <Button asChild variant="outline">
              <Link href="/">New analysis</Link>
            </Button>
          </div>
        </div>
      </div>
    );
  }

  const { report } = state;
  const officialRevision = (revisions ?? []).find((revision) => revision.isOfficial);
  const heading = reportHeading(mode === 'edit' && editBase ? editBase.report : report);
  const filename = (mode === 'edit' && editBase ? editBase.report : report).run.filename;
  const changes = draft && editBase ? draftChangeCount(editBase.report.structured, draft) : 0;
  const problems = draft ? draftProblems(draft) : [];
  const otherAiRevision = (revisions ?? []).find((revision) => revision.kind === 'ai' && revision.revisionId !== report.revisionId);
  const compareHref = otherAiRevision
    ? `/reports/${encodeURIComponent(videoId)}/compare?left=${report.revisionId}&right=${otherAiRevision.revisionId}`
    : null;
  const officialLabel = officialRevision ? `v${officialRevision.revisionNo}` : 'none';

  const changeDraft = (next: EditDraft) => {
    if (draft) setUndoStack((stack) => [...stack.slice(-49), draft]);
    setDraft(next);
  };

  const undoDraft = () => {
    const previous = undoStack.at(-1);
    if (!previous) return;
    setDraft(previous);
    setUndoStack((stack) => stack.slice(0, -1));
    setSaveProblems([]);
  };

  const revertDraft = () => {
    if (!editBase) return;
    setDraft(draftFromView(editBase.report));
    setUndoStack([]);
    setSaveProblems([]);
  };

  const resizeHeightAt = (clientY: number) => {
    const band = reportBandRef.current?.getBoundingClientRect();
    if (!band) return;
    setBandHeight(resizeReportBandHeight(clientY - band.top));
  };

  const startHeightResize = (event: React.PointerEvent<HTMLHRElement>) => {
    resizingHeightRef.current = true;
    event.currentTarget.setPointerCapture(event.pointerId);
    resizeHeightAt(event.clientY);
  };

  const moveHeightResize = (event: React.PointerEvent<HTMLHRElement>) => {
    if (resizingHeightRef.current) resizeHeightAt(event.clientY);
  };

  const finishHeightResize = (event: React.PointerEvent<HTMLHRElement>) => {
    resizingHeightRef.current = false;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  function switchMode(next: Mode) {
    if (next === 'view') {
      setMode('view');
      setSaveOpen(false);
      return;
    }
    if (draft && editBase) setMode('edit');
    else startEdit(report);
  }

  async function save() {
    if (!draft || !editBase || saving) return;
    const trimmed = note.trim();
    if (!trimmed) {
      setSaveProblems(['Add a note for the new version.']);
      return;
    }
    if (problems.length) {
      setSaveProblems(problems);
      return;
    }
    setSaving(true);
    setSaveProblems([]);
    try {
      // Crops held in the browser reach R2 only now, before the revision that references them is written.
      const keys = new Map<string, string>();
      for (const { itemId, blob } of pendingImages(draft)) keys.set(itemId, await uploadEvidenceImage(videoId, itemId, blob));
      const response = await fetch(`/api/reports/${encodeURIComponent(videoId)}/edit`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          parentId: editBase.revisionId,
          editedBy: SAVE_AUTHOR,
          note: trimmed,
          edit: editFromDraft(withUploadedImages(draft, keys)),
        }),
      });
      const payload = (await response.json()) as {
        error?: string;
        violations?: Array<{ message: string }>;
        revision?: { revisionId: number };
      };
      if (!response.ok || !payload.revision) {
        setSaveProblems(
          payload.violations?.length ? payload.violations.map((violation) => violation.message) : [payload.error || 'Save failed'],
        );
        return;
      }
      const savedId = payload.revision.revisionId;
      setDraft(null);
      setUndoStack([]);
      setEditBase(null);
      setCompareReport(null);
      setCompareRevisionId(null);
      setMode('view');
      setSaveOpen(false);
      setNote('');
      setAttempt((value) => value + 1);
      router.push(`/reports/${encodeURIComponent(videoId)}?revision=${savedId}`);
    } catch (cause) {
      setSaveProblems([cause instanceof Error ? cause.message : 'Save failed']);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="pb-16">
      <article className="report-sheet overflow-clip border-y border-border bg-card shadow-panel" data-testid="report-page">
        {/* One sticky unit from lg: the back bar, the video and the versions stay together. Below lg only the back bar sticks (`contents` lets it stick to the article), because the stacked band is too tall to pin. */}
        <div className="contents lg:sticky lg:top-0 lg:z-20 lg:block" data-testid="report-sticky">
          <div className="z-30 flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border bg-card px-5 py-3 text-sm max-lg:sticky max-lg:top-0 sm:px-8">
            <Link className="font-semibold text-primary hover:underline" href="/library" id="back-to-library">
              ← Library
            </Link>
            <h1 className="min-w-0 flex-1 truncate text-sm font-semibold" title={filename}>
              {heading}
              <span className="ml-2 font-normal text-muted-foreground">{filename}</span>
            </h1>
            {report.privacy.runBlurred && (
              <Badge
                data-testid="faces-blurred-badge"
                title="The faces in this version's evidence frames, crops and clips are blurred"
                variant="secondary"
              >
                Faces blurred
              </Badge>
            )}
            <PrivacyControls blurFaces={report.privacy.blurFaces} onAnalysed={showAnalysedVersion} videoId={videoId} />
            <ReanalysisDialog onAttempted={() => setAttempt((value) => value + 1)} videoId={videoId} />
            <Button
              aria-expanded={chatOpen}
              className="text-xs font-semibold"
              data-testid="chat-toggle"
              onClick={() => setChatOpen((open) => !open)}
              size="sm"
              type="button"
              variant="outline"
            >
              Ask about this incident
            </Button>
            <Tabs onValueChange={(value) => switchMode(value as Mode)} value={mode}>
              <TabsList aria-label="Report mode" className="h-8">
                <TabsTrigger className="text-xs font-semibold" value="view">
                  View
                </TabsTrigger>
                <TabsTrigger className="text-xs font-semibold" value="edit">
                  Edit
                </TabsTrigger>
              </TabsList>
            </Tabs>
          </div>

          <div
            className="relative border-b border-border bg-card lg:h-(--report-band-height)"
            data-testid="report-band"
            ref={reportBandRef}
            style={
              {
                '--report-band-height': `${bandHeight}px`,
                '--video-aspect': String(videoAspect),
              } as CSSProperties
            }
          >
            <ResizablePanelGroup
              className="max-lg:!flex-col max-lg:!h-auto max-lg:!overflow-visible [&>[data-panel]]:max-lg:!basis-auto [&>[data-panel]]:max-lg:!w-full [&>[data-panel]]:max-lg:!max-w-full [&>[data-panel]]:max-lg:!h-auto"
              orientation="horizontal"
            >
              <ResizablePanel
                className="overflow-hidden lg:h-full"
                defaultSize={`${DEFAULT_TWO_PANE_BAND_WIDTHS.video * 100}%`}
                maxSize="85%"
                minSize="15%"
              >
                <div
                  className="video-cell relative flex min-h-0 min-w-0 items-center justify-center overflow-hidden bg-muted [container-type:size] max-lg:aspect-(--video-aspect) lg:h-full"
                  data-testid="video-cell"
                >
                  <div
                    className="relative max-h-full max-w-full overflow-hidden bg-black"
                    style={{ aspectRatio: videoAspect, width: `min(100cqw, calc(100cqh * ${videoAspect}))` }}
                  >
                    <video
                      className="h-full w-full object-contain"
                      controls
                      onLoadedMetadata={(event) => {
                        const { duration: length, videoWidth, videoHeight } = event.currentTarget;
                        setPlayerDuration(Number.isFinite(length) && length > 0 ? length : null);
                        if (videoWidth > 0 && videoHeight > 0) setVideoAspect(videoWidth / videoHeight);
                        if (startSeconds !== null && !startedAt.current) {
                          startedAt.current = true;
                          event.currentTarget.currentTime = startSeconds;
                        }
                      }}
                      onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
                      ref={videoRef}
                      src={report.run.playbackUrl}
                    >
                      <track kind="captions" />
                    </video>
                    {mode === 'view' && !inPip && trackedItems.length > 0 && (
                      <TrackOverlay
                        frameAspect={videoAspect}
                        items={trackedItems.map((item) => ({
                          id: item.id,
                          label: item.label,
                          colour: item.colour,
                          trackUrl: item.trackUrl,
                        }))}
                        selectedId={selectedTrackId}
                        video={videoRef}
                      />
                    )}
                    {inPip && (
                      <div
                        className="absolute inset-0 grid place-items-center bg-black/90 p-3 text-center text-xs text-white"
                        data-testid="pip-placeholder"
                      >
                        Playing in picture-in-picture
                      </div>
                    )}
                    {pipSupported && (
                      <Button
                        aria-label={inPip ? 'Return video to the page' : 'Open video in picture-in-picture'}
                        aria-pressed={inPip}
                        className="absolute top-2 right-2 z-10 bg-black/60 text-white hover:bg-black/80 hover:text-white"
                        data-testid="pip-toggle"
                        onClick={() => void togglePip()}
                        size="icon-sm"
                        title={inPip ? 'Return video to the page' : 'Picture-in-picture: keep the video floating while you work'}
                        type="button"
                        variant="ghost"
                      >
                        <PictureInPicture2Icon aria-hidden="true" />
                      </Button>
                    )}
                  </div>
                </div>
              </ResizablePanel>
              <ResizableHandle aria-label="Resize video and version history" className="hidden lg:flex" withHandle />
              <ResizablePanel
                className="min-h-0 overflow-hidden border-t border-border lg:border-t-0 max-lg:max-h-72 lg:h-full"
                defaultSize={`${DEFAULT_TWO_PANE_BAND_WIDTHS.versions * 100}%`}
                maxSize="85%"
                minSize="15%"
              >
                <ReportVersions
                  compareRevisionId={mode === 'edit' ? compareRevisionId : null}
                  currentRevisionId={report.revisionId}
                  error={revisionsError}
                  mode={mode}
                  onCompare={(id) => setCompareRevisionId(id)}
                  onOfficialChanged={() => setAttempt((value) => value + 1)}
                  onOpen={openVersion}
                  revisions={revisions}
                  videoId={videoId}
                />
              </ResizablePanel>
            </ResizablePanelGroup>
            <ReportBandHeightHandle
              height={bandHeight}
              onFinish={finishHeightResize}
              onMove={moveHeightResize}
              onNudge={(direction) => setBandHeight((height) => nudgeReportBandHeight(height, direction))}
              onStart={startHeightResize}
            />
          </div>
        </div>

        {mode === 'edit' && !officialRevision && revisions && (
          <Alert
            className="rounded-none border-x-0 border-t-0 border-b border-amber-300 bg-amber-50/80 px-5 py-2 text-xs text-amber-950"
            data-testid="official-status"
          >
            <AlertDescription>No official version has been selected for this video. Star a version to make it official.</AlertDescription>
          </Alert>
        )}

        {mode === 'edit' && compareError && (
          <Alert
            className="rounded-none border-x-0 border-t-0 border-b border-destructive/20 bg-destructive/10 px-5 py-2 text-xs text-destructive"
            data-testid="compare-error"
            variant="destructive"
          >
            <AlertDescription>Could not load the A version: {compareError}</AlertDescription>
          </Alert>
        )}

        {mode === 'view' ? (
          <ReportPane
            compareHref={compareHref}
            officialMissing={!officialRevision && revisions !== null}
            onSeek={seek}
            onSelectTrack={selectTrack}
            playerDuration={playerDuration}
            report={report}
            selectedTrackId={selectedTrackId}
          />
        ) : draft && editBase ? (
          <ReportEditPane
            a={compareReport?.structured ?? null}
            aRevisionNo={compareReport?.revision.revisionNo ?? editBase.revisionNo}
            baseRevisionNo={editBase.revisionNo}
            draft={draft}
            onChange={changeDraft}
            onSeek={seek}
            title={reportHeading(editBase.report)}
            videoId={videoId}
            videoSrc={report.run.playbackUrl}
          />
        ) : null}

        <div className="sticky bottom-0 z-20 flex flex-col gap-2 border-t border-border bg-card/95 px-5 py-3 text-sm backdrop-blur-sm sm:flex-row sm:items-center sm:justify-between sm:gap-3 sm:px-8">
          {mode === 'view' ? (
            <>
              <span className="text-muted-foreground">
                <b>v{report.revision.revisionNo}</b>{' '}
                {report.official.isOfficial ? 'is the official version' : 'is not the official version'}
              </span>
              <Button onClick={() => startEdit(report)} type="button">
                Edit from v{report.revision.revisionNo}
              </Button>
            </>
          ) : (
            <>
              <span className="min-w-0 text-muted-foreground sm:flex-1">
                {editBase && (
                  <>
                    <b>Draft</b> from v{editBase.revisionNo} ·{' '}
                    {changes ? (
                      <>
                        <b>{changes}</b> unsaved change{changes === 1 ? '' : 's'}
                      </>
                    ) : (
                      'no changes yet'
                    )}{' '}
                    · official is <b>{officialLabel}</b>
                  </>
                )}
              </span>
              <div className="flex flex-wrap items-center gap-2">
                <Button disabled={undoStack.length === 0 || saving} onClick={undoDraft} size="sm" type="button" variant="outline">
                  Undo
                </Button>
                <Button
                  aria-label="Revert all draft changes"
                  disabled={changes === 0 || saving}
                  onClick={() => setRevertConfirmOpen(true)}
                  size="sm"
                  type="button"
                  variant="outline"
                >
                  Revert
                </Button>
                <Button disabled={changes === 0 || saving} onClick={() => setSaveOpen(true)} type="button">
                  Save as new version
                </Button>
              </div>
            </>
          )}
        </div>
      </article>

      <AlertDialog onOpenChange={setRevertConfirmOpen} open={revertConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revert all draft changes?</AlertDialogTitle>
            <AlertDialogDescription>
              This will discard all unsaved edits made to this draft and return to v{editBase?.revisionNo}.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setRevertConfirmOpen(false);
                revertDraft();
              }}
            >
              Revert
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog
        onOpenChange={(open) => {
          setSaveOpen(open);
          if (!open) setSaveProblems([]);
        }}
        open={saveOpen}
      >
        <DialogContent className="sm:max-w-md" data-testid="save-note">
          <DialogHeader>
            <DialogTitle>Save as new version</DialogTitle>
            <DialogDescription>Record what changed in this version before saving.</DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <Label className="text-xs font-bold" htmlFor="savenote">
              Note for the new version (required)
            </Label>
            <Textarea
              aria-label="Note for the new version"
              className="mt-1 min-h-[84px]"
              id="savenote"
              onChange={(event) => setNote(event.target.value)}
              placeholder="What changed and why, e.g. raised severity to 4 and added the phone"
              value={note}
            />
            {saveProblems.length > 0 && (
              <ul aria-live="polite" className="mt-2 list-disc pl-5 text-xs text-destructive" data-testid="save-problems">
                {saveProblems.map((problem) => (
                  <li key={problem}>{problem}</li>
                ))}
              </ul>
            )}
          </div>
          <DialogFooter>
            <Button
              disabled={saving}
              onClick={() => {
                setSaveOpen(false);
                setSaveProblems([]);
              }}
              type="button"
              variant="outline"
            >
              Cancel
            </Button>
            <Button disabled={saving || !note.trim()} onClick={() => void save()} type="button">
              {saving ? 'Saving…' : 'Save as new version'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Sheet onOpenChange={setChatOpen} open={chatOpen}>
        <SheetContent className="flex flex-col p-0 sm:max-w-md" side="right">
          <SheetHeader className="sr-only">
            <SheetTitle>Ask about this incident</SheetTitle>
            <SheetDescription>Conversational questions and answers about this incident</SheetDescription>
          </SheetHeader>
          {(() => {
            const target = chatTarget(
              mode === 'edit' && draft && editBase
                ? { revision: editBase.report.revision, isOfficial: editBase.report.official.isOfficial, draft }
                : { revision: report.revision, isOfficial: report.official.isOfficial, draft: null },
            );
            // Keyed by the version (and whether it is a draft), so changing version starts a new conversation.
            return (
              <ReportChat
                key={`${target.versionId}-${mode === 'edit' && draft ? 'draft' : 'saved'}`}
                onSeek={seek}
                target={target}
                videoId={videoId}
              />
            );
          })()}
        </SheetContent>
      </Sheet>
    </div>
  );
}

function ReportBandHeightHandle({
  height,
  onStart,
  onMove,
  onFinish,
  onNudge,
}: {
  height: number;
  onStart: (event: React.PointerEvent<HTMLHRElement>) => void;
  onMove: (event: React.PointerEvent<HTMLHRElement>) => void;
  onFinish: (event: React.PointerEvent<HTMLHRElement>) => void;
  onNudge: (direction: -1 | 1) => void;
}) {
  return (
    <hr
      aria-label="Resize video and version history height"
      aria-orientation="horizontal"
      aria-valuemax={MAX_REPORT_BAND_HEIGHT}
      aria-valuemin={MIN_REPORT_BAND_HEIGHT}
      aria-valuenow={height}
      className="absolute inset-x-0 bottom-0 z-40 hidden h-4 translate-y-1/2 cursor-row-resize touch-none border-0 after:absolute after:inset-x-0 after:top-1/2 after:h-px after:bg-border hover:after:bg-primary focus:after:bg-primary lg:block"
      onKeyDown={(event) => {
        if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
        event.preventDefault();
        onNudge(event.key === 'ArrowUp' ? -1 : 1);
      }}
      onPointerCancel={onFinish}
      onPointerDown={onStart}
      onPointerMove={onMove}
      onPointerUp={onFinish}
      tabIndex={0}
    />
  );
}

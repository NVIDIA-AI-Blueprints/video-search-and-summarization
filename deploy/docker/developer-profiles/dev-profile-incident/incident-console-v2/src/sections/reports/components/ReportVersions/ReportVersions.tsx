// SPDX-License-Identifier: Apache-2.0
'use client';

import { Loader2 } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useState } from 'react';

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
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { clearOfficial, markOfficial } from '@/lib/reports/official-client';
import { officialRevisionIdOf, shouldConfirmReplace } from '@/lib/reports/official-state';
import { layoutVersions } from '@/lib/reports/version-graph';
import { formatTimestamp } from '@/lib/time';
import type { RevisionSummary } from '@/modules/reports/domain/revision';

const LANE_WIDTH = 16;

/** The incident's versions as branches, newest first. A star marks a version official; the filled star clears it. A saved version is never changed. */
export function ReportVersions({
  videoId,
  revisions,
  error,
  currentRevisionId,
  onOpen,
  onOfficialChanged,
  mode = 'view',
  onEditRevision,
}: {
  videoId: string;
  revisions: RevisionSummary[] | null;
  error: string | null;
  currentRevisionId: number;
  onOpen: (revisionId: number) => void;
  onOfficialChanged: () => void;
  /** Edit mode compares the draft with an A version instead of opening one to read. */
  mode?: 'view' | 'edit';
  onEditRevision?: (revisionId: number) => void;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingRevision, setPendingRevision] = useState<RevisionSummary | null>(null);
  // The answer of the last successful mark or clear, until the refetched list carries it.
  const [localOfficialId, setLocalOfficialId] = useState<number | null | undefined>(undefined);
  // biome-ignore lint/correctness/useExhaustiveDependencies: a new list is the server's answer, which replaces the local one
  useEffect(() => setLocalOfficialId(undefined), [revisions]);
  const officialId = officialRevisionIdOf(revisions ?? [], localOfficialId);

  async function executeToggle(revision: RevisionSummary) {
    setBusyId(revision.revisionId);
    setActionError(null);
    try {
      const clearing = revision.revisionId === officialId;
      await (clearing ? clearOfficial(videoId) : markOfficial(videoId, revision.revisionId));
      setLocalOfficialId(clearing ? null : revision.revisionId);
      onOfficialChanged();
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : 'Update failed');
    } finally {
      setBusyId(null);
    }
  }

  function toggleOfficial(revision: RevisionSummary) {
    if (shouldConfirmReplace(revisions ?? [], revision.revisionId, localOfficialId)) {
      setPendingRevision(revision);
      return;
    }
    void executeToggle(revision);
  }

  const layout = revisions ? layoutVersions(revisions) : null;
  return (
    <TooltipProvider>
      <section aria-label="Versions" className="flex min-h-0 flex-col" data-testid="revision-history">
        <div className="flex items-center justify-between gap-2 px-3 pt-2">
          <h3 className="font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-muted-foreground">
            Versions{revisions ? ` · ${revisions.length}` : ''}
          </h3>
          <Link className="text-[11px] font-semibold text-primary hover:underline" href={`/videos/${encodeURIComponent(videoId)}`}>
            Analysis history
          </Link>
        </div>
        {error && <p className="px-3 py-2 text-xs text-destructive">{error}</p>}
        {actionError && (
          <Alert
            className="mx-3 my-1 border-destructive/20 bg-destructive/10 p-2 text-xs text-destructive"
            data-testid="official-error"
            role="alert"
            variant="destructive"
          >
            <AlertDescription>{actionError}</AlertDescription>
          </Alert>
        )}
        {!revisions && !error && (
          <div className="space-y-2 px-3 py-2">
            <Skeleton className="h-10 w-full rounded-xl" />
            <Skeleton className="h-10 w-full rounded-xl" />
            <Skeleton className="h-10 w-full rounded-xl" />
          </div>
        )}
        {layout && (
          <ScrollArea className="min-h-0 px-2 py-1">
            <ol>
              {layout.rows.map(({ revision, lane, segments }) => {
                const current = revision.revisionId === currentRevisionId;
                const official = revision.revisionId === officialId;
                const busy = revision.revisionId === busyId;
                const starLabel = official
                  ? `Clear v${revision.revisionNo} as the official version`
                  : `Mark v${revision.revisionNo} official`;
                const starTitle = busy ? 'Saving' : official ? 'Official version: click to clear' : 'Mark official';

                return (
                  <li
                    className={`flex items-stretch gap-2 rounded-xl px-1 py-1.5 text-xs ${current ? 'bg-primary/10' : ''}`}
                    data-testid="revision-row"
                    key={revision.revisionId}
                  >
                    <span aria-hidden className="relative shrink-0" style={{ width: layout.laneCount * LANE_WIDTH }}>
                      {segments.map((segment) => (
                        <i
                          className="absolute w-px bg-border"
                          key={segment.lane}
                          style={{
                            left: segment.lane * LANE_WIDTH + LANE_WIDTH / 2,
                            top: segment.fromAbove ? 0 : '50%',
                            bottom: segment.toBelow ? 0 : '50%',
                          }}
                        />
                      ))}
                      <i
                        className={`absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 ${revision.kind === 'human' ? 'border-primary bg-primary' : 'border-muted-foreground/60 bg-card'}`}
                        style={{ left: lane * LANE_WIDTH + LANE_WIDTH / 2 }}
                      />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-x-2">
                        <b>v{revision.revisionNo}</b>
                        <span className="text-muted-foreground">{formatTimestamp(revision.createdAt, 'time unknown')}</span>
                        {official && (
                          <Badge className="border-amber-400 bg-amber-50 text-[10.5px] font-bold text-amber-800" variant="outline">
                            ★ Official
                          </Badge>
                        )}
                      </span>
                      <span className="flex items-center gap-1.5 text-muted-foreground">
                        <Badge className="px-1.5 text-[10.5px] font-semibold" variant="secondary">
                          {revision.kind === 'human' ? 'Human' : 'AI'}
                        </Badge>
                        <span className="truncate">
                          {revision.kind === 'ai' ? (revision.model ?? 'Unknown model') : (revision.note ?? '')}
                        </span>
                      </span>
                    </span>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          aria-label={starLabel}
                          aria-busy={busy}
                          aria-pressed={official}
                          className="h-7 w-7 self-center p-0 text-base leading-none text-amber-600 hover:text-amber-700 disabled:opacity-40"
                          data-testid={official ? 'clear-official' : 'make-official'}
                          disabled={busyId !== null}
                          onClick={() => void toggleOfficial(revision)}
                          title={starTitle}
                          type="button"
                          variant="ghost"
                        >
                          {busy ? <Loader2 aria-hidden className="size-3.5 animate-spin" /> : official ? '★' : '☆'}
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>{starTitle}</TooltipContent>
                    </Tooltip>
                    {mode === 'edit' && current ? (
                      <Badge className="self-center px-2 py-0.5 text-[10.5px] font-bold" variant="default">
                        Editing
                      </Badge>
                    ) : mode === 'edit' ? (
                      <Button
                        aria-label={`Edit from v${revision.revisionNo}`}
                        className="h-6 self-center px-2 text-[10.5px] font-bold"
                        onClick={() => onEditRevision?.(revision.revisionId)}
                        size="sm"
                        type="button"
                        variant="outline"
                      >
                        Edit
                      </Button>
                    ) : current ? (
                      <Badge className="self-center px-2 py-0.5 text-[10.5px] font-bold" variant="default">
                        Viewing
                      </Badge>
                    ) : (
                      <Button
                        className="h-6 self-center px-2 text-[10.5px] font-semibold"
                        onClick={() => onOpen(revision.revisionId)}
                        size="sm"
                        type="button"
                        variant="outline"
                      >
                        View
                      </Button>
                    )}
                  </li>
                );
              })}
            </ol>
          </ScrollArea>
        )}

        <AlertDialog onOpenChange={(open) => !open && setPendingRevision(null)} open={pendingRevision !== null}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Replace official report?</AlertDialogTitle>
              <AlertDialogDescription>
                Another version is currently the official report for this video. Replace it with this one?
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction
                onClick={() => {
                  if (pendingRevision) {
                    const target = pendingRevision;
                    setPendingRevision(null);
                    void executeToggle(target);
                  }
                }}
              >
                Replace official
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </section>
    </TooltipProvider>
  );
}

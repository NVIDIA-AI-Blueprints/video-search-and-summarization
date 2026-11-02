// SPDX-License-Identifier: Apache-2.0
'use client';

import { FilmIcon, Loader2Icon, PlayIcon } from 'lucide-react';
import { useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { evidenceDisplay } from '@/lib/evidence/evidence-view';
import { type EvidenceDetailItem, evidenceDetailLines } from '@/modules/reports/domain/evidence-detail';
import { type ClipState, clipButton } from '@/modules/reports/domain/evidence-clip';
import type { TrackedReportItem } from '@/modules/reports/domain/track';
import { EvidenceFrame, KIND_LABEL } from '@/sections/evidence/components/EvidenceFrame';
import { PresenceBar } from '@/sections/reports/components/PresenceBar';

/**
 * One evidence item in detail: its whole frame with the box, large, the full description and Play from here, which closes
 * the pop-up and seeks the page's video to the item's second. A tracked item also shows the span the tracker followed it
 * and its presence bar, whose click closes the pop-up and seeks there. An item with a clip has Play clip, which asks the
 * console for the clip (cut on the first play, then kept in R2) and plays it under the frame. Closes with Escape, the close
 * button or a click outside.
 */
export function EvidenceDetailDialog({
  item,
  videoId,
  onClose,
  onPlayFrom,
  track = null,
  playerDuration = null,
}: {
  item: EvidenceDetailItem | null;
  /** The report's video; an older item's frame is cut from its stored file. */
  videoId: string;
  onClose: () => void;
  onPlayFrom: (seconds: number) => void;
  /** The item's track, when it has one. */
  track?: TrackedReportItem | null;
  playerDuration?: number | null;
}) {
  return (
    <Dialog open={item !== null} onOpenChange={(open) => !open && onClose()}>
      {item && (
        <Detail
          item={item}
          key={`${item.kind}:${item.id}`}
          onClose={onClose}
          onPlayFrom={onPlayFrom}
          playerDuration={playerDuration}
          track={track}
          videoId={videoId}
        />
      )}
    </Dialog>
  );
}

function Detail({
  item,
  videoId,
  onClose,
  onPlayFrom,
  track,
  playerDuration,
}: {
  item: EvidenceDetailItem;
  videoId: string;
  onClose: () => void;
  onPlayFrom: (seconds: number) => void;
  track: TrackedReportItem | null;
  playerDuration: number | null;
}) {
  const { seconds } = item;
  const lines = evidenceDetailLines(item);
  const shown = evidenceDisplay(item.evidence, videoId);
  const clipUrl = item.evidence?.clipUrl ?? null;
  const [clip, setClip] = useState<ClipState>({ status: 'idle' });
  const clipControl = clipButton(clip);
  const playClip = async () => {
    if (!clipUrl) return;
    setClip({ status: 'loading' });
    try {
      const response = await fetch(clipUrl, { method: 'POST' });
      const body = (await response.json().catch(() => ({}))) as { url?: unknown; error?: unknown };
      if (response.ok && typeof body.url === 'string') setClip({ status: 'ready', url: body.url });
      else
        setClip({
          status: 'error',
          message: typeof body.error === 'string' ? body.error : `The clip could not be cut (HTTP ${response.status})`,
        });
    } catch {
      setClip({ status: 'error', message: 'The clip could not be fetched' });
    }
  };
  return (
    <DialogContent className="max-h-[calc(100dvh-2rem)] auto-rows-max overflow-y-auto sm:max-w-3xl" data-testid="evidence-dialog">
      <DialogHeader className="pr-8">
        <DialogTitle className="flex items-center gap-2 text-lg">
          <Badge className="shrink-0 px-1.5 py-0 font-mono text-[10px] font-bold" variant="outline">
            {item.id}
          </Badge>
          <span className="min-w-0">{item.title}</span>
        </DialogTitle>
        <DialogDescription className="font-mono text-xs" data-testid="evidence-dialog-time">
          {KIND_LABEL[item.kind]}
          {lines.time && ` · most visible at ${lines.time}`}
        </DialogDescription>
      </DialogHeader>
      <EvidenceFrame
        aspect={16 / 9}
        box={shown.box}
        frameSrc={shown.frameSrc}
        fullFrame
        kind={item.kind}
        label={`${item.title} image`}
        notLocated={shown.notLocated}
        seconds={seconds}
        src={shown.src}
      />
      {clip.status === 'ready' && (
        // biome-ignore lint/a11y/useMediaCaption: the clip has no audio track, so there is nothing to caption
        <video
          autoPlay
          className="aspect-video w-full rounded-md bg-black"
          controls
          data-testid="evidence-dialog-clip"
          playsInline
          src={clip.url}
        />
      )}
      {clip.status === 'error' && (
        <p className="text-xs text-destructive" data-testid="evidence-dialog-clip-error" role="alert">
          {clip.message}
        </p>
      )}
      <div className="grid gap-2 text-sm leading-6">
        <p className="whitespace-pre-wrap text-foreground/80" data-testid="evidence-dialog-description">
          {lines.description}
        </p>
        {lines.holds && (
          <p>
            Holds <b>{lines.holds}</b>
          </p>
        )}
        {lines.heldBy && (
          <p>
            Held by <b>{lines.heldBy}</b>
          </p>
        )}
        {lines.threat && <p className="text-muted-foreground">{lines.threat}</p>}
        {lines.onScreen && <p className="text-muted-foreground">{lines.onScreen}</p>}
        {lines.trackedOnScreen && (
          <p className="text-muted-foreground" data-testid="evidence-dialog-tracked">
            {lines.trackedOnScreen}
          </p>
        )}
        {track && (
          <PresenceBar
            caption={false}
            colour={track.colour}
            durationSeconds={playerDuration}
            firstSeconds={track.firstSeconds}
            label={item.title}
            lastSeconds={track.lastSeconds}
            onSeek={(at) => {
              onClose();
              onPlayFrom(at);
            }}
          />
        )}
      </div>
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">{lines.playNote}</p>
        <div className="flex items-center gap-2">
          {clipUrl && clipControl.visible && (
            <Button
              data-testid="evidence-dialog-clip-button"
              disabled={clipControl.disabled}
              onClick={() => void playClip()}
              type="button"
              variant="outline"
            >
              {clip.status === 'loading' ? <Loader2Icon className="animate-spin" /> : <FilmIcon />}
              {clipControl.label}
            </Button>
          )}
          <Button
            data-testid="evidence-dialog-play"
            disabled={seconds === null}
            onClick={() => {
              if (seconds === null) return;
              onClose();
              onPlayFrom(seconds);
            }}
            type="button"
          >
            <PlayIcon />
            Play from here
          </Button>
        </div>
      </div>
    </DialogContent>
  );
}

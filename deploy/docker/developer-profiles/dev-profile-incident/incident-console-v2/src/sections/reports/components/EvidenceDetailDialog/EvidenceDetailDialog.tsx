// SPDX-License-Identifier: Apache-2.0
'use client';

import { PlayIcon } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { EvidenceBadges } from '@/sections/reports/components/EvidenceBadges';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { evidenceDisplay } from '@/lib/evidence/evidence-view';
import { type EvidenceDetailItem, evidenceDetailLines } from '@/modules/reports/domain/evidence-detail';
import { type StillBox, stillBoxFor, type TrackedReportItem } from '@/modules/reports/domain/track';
import { EvidenceFrame, KIND_LABEL } from '@/sections/evidence/components/EvidenceFrame';
import { PresenceBar } from '@/sections/reports/components/PresenceBar';

/**
 * One evidence item in detail: its whole frame with the box, large, the full description and Play from here, which closes
 * the pop-up and seeks the page's video to the item's second. A tracked item also shows the span the tracker followed it
 * and its presence bar, whose click closes the pop-up and seeks there.
 * Closes with Escape, the close button or a click outside.
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
  /** An untracked item passes its still box: the page seeks to the box's second, pauses and holds the box. */
  onPlayFrom: (seconds: number, still?: StillBox | null) => void;
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
  onPlayFrom: (seconds: number, still?: StillBox | null) => void;
  track: TrackedReportItem | null;
  playerDuration: number | null;
}) {
  const { seconds } = item;
  const lines = evidenceDetailLines(item);
  const shown = evidenceDisplay(item.evidence, videoId);
  const still = stillBoxFor(item);
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
        <EvidenceBadges badges={lines.badges} />
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
        {lines.notTracked && (
          <p className="text-xs text-muted-foreground" data-testid="evidence-dialog-not-tracked">
            {lines.notTracked}
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
          <Button
            data-testid="evidence-dialog-play"
            disabled={seconds === null}
            onClick={() => {
              if (seconds === null) return;
              onClose();
              // An item with no track has only its stored box: seek to the second it was found, pause and hold the box.
              if (still) onPlayFrom(still.seconds, still);
              else onPlayFrom(seconds);
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

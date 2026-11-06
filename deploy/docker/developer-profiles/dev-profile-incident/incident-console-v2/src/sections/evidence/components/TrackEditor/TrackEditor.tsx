// SPDX-License-Identifier: Apache-2.0

'use client';

import { ChevronLeftIcon, ChevronRightIcon, CircleHelpIcon, PauseIcon, PlayIcon } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

import { MutedVideo } from '@/components/muted-video';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import type { TrackEditorBadge } from '@/lib/evidence/track-editor-source';
import type { ReviewerTrack } from '@/modules/reports/domain/reviewer-item';
import type { EvidenceItemType } from '@/modules/reports/domain/revision';
import { parseTrackSamples, type TrackSample } from '@/modules/reports/domain/track';
import {
  type Box,
  boxAt,
  deleteKeyframe,
  densify,
  type EditorTrack,
  formatEditorClock,
  keyframeIndexAt,
  type StillBoxAt,
  SNAP_SECONDS,
  setKeyframe,
  setStart,
  setStop,
  snapTenth,
  timelineWindow,
  trackFromSamples,
} from '@/modules/reports/domain/track-editor';
import { TrackFrame } from './TrackFrame';
import { TrackTimeline } from './TrackTimeline';

const KIND_LABEL: Record<EvidenceItemType, string> = { entity: 'Entity', instrument: 'Instrument', asset: 'Asset' };

const SHORTCUTS: Array<[string, string]> = [
  ['Space', 'Play or pause'],
  ['← →', 'Step 0.1 s (Shift: 1 s)'],
  ['K', 'Add a keyframe at the playhead'],
  ['I / O', 'Start / stop the track at the playhead'],
  ['Delete', 'Delete the selected keyframe'],
  ['Cmd/Ctrl+Z', 'Undo'],
  ['Esc', 'Close help, else clear the selection'],
  ['?', 'Show or hide this list'],
];

const NEW_TRACK_HINT = 'Track started. Drag the grips to set where it starts and stops.';

async function readSamples(url: string, signal: AbortSignal): Promise<TrackSample[]> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`The saved track could not be read (${response.status}).`);
  const samples = parseTrackSamples(((await response.json()) as { samples?: unknown } | null)?.samples);
  if (!samples?.length) throw new Error('The saved track has no samples.');
  return samples;
}

/**
 * The track editor (Edit mode, "Edit track"): fix an item's track with a few keyframes instead of a box per frame. One
 * frame with the box, one timeline under it, one footer. Dragging the box makes a keyframe at the playhead; the grips set
 * where the item is on screen; Done expands the keyframes to plain samples for the draft (Save as new version stores them).
 * The model is `modules/reports/domain/track-editor.ts`; this is the interaction around it.
 */
export function TrackEditor({
  videoSrc,
  name,
  kind,
  itemId = null,
  badge,
  samples = null,
  trackUrl = null,
  still = null,
  initialSeconds = 0,
  onClose,
  onDone,
}: {
  videoSrc: string;
  name: string;
  kind: EvidenceItemType;
  itemId?: string | null;
  badge: TrackEditorBadge;
  /** Samples already in hand (a reviewer's track), else read from `trackUrl`. */
  samples?: TrackSample[] | null;
  trackUrl?: string | null;
  /** A saved still box, for an item with no track. */
  still?: StillBoxAt | null;
  initialSeconds?: number;
  onClose: () => void;
  onDone: (track: ReviewerTrack) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [track, setTrack] = useState<EditorTrack | null>(null);
  const [history, setHistory] = useState<Array<EditorTrack | null>>([]);
  const [seconds, setSeconds] = useState(initialSeconds);
  const [selected, setSelected] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [help, setHelp] = useState(false);
  const [whole, setWhole] = useState(false);
  const [duration, setDuration] = useState<number | null>(null);
  const [loading, setLoading] = useState(!samples && !!trackUrl);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [view, setView] = useState<{ from: number; to: number } | null>(null);
  const current = useRef({ track, seconds, duration, selected, playing });
  current.current = { track, seconds, duration, selected, playing };

  // Load: the reviewer's samples, or the saved track from the track route; either is thinned to a few keyframes.
  useEffect(() => {
    const start = (loaded: EditorTrack | null) => {
      setTrack(loaded);
      if (loaded) setSeconds(loaded.start);
      else if (still) setSeconds(still.t);
    };
    if (samples) return start(trackFromSamples(samples));
    if (!trackUrl) return start(null);
    const controller = new AbortController();
    readSamples(trackUrl, controller.signal)
      .then((loaded) => start(trackFromSamples(loaded)))
      .catch((cause) => {
        if (!controller.signal.aborted) setLoadError(cause instanceof Error ? cause.message : 'The saved track could not be read.');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [samples, trackUrl, still]);

  const pause = () => {
    if (!current.current.playing) return;
    videoRef.current?.pause();
    setPlaying(false);
    setSeconds((value) => snapTenth(value));
  };

  // While paused the video shows the playhead's second; while playing the video drives the playhead.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || playing || duration === null) return;
    if (Math.abs(video.currentTime - seconds) > 0.02) video.currentTime = seconds;
  }, [seconds, playing, duration]);
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !playing) return;
    let frame = 0;
    const tick = () => {
      const end = current.current.duration ?? video.duration;
      if (video.ended || video.currentTime >= end - 0.05) {
        video.pause();
        setPlaying(false);
        setSeconds(snapTenth(Math.min(video.currentTime, end)));
        return;
      }
      setSeconds(video.currentTime);
      frame = requestAnimationFrame(tick);
    };
    video.currentTime = current.current.seconds;
    void video.play().catch(() => setPlaying(false));
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing]);

  // The timeline shows a window around the track (the whole clip when short, or on request); it follows the track and playhead.
  useEffect(() => {
    if (duration === null || loading) return;
    setView((now) => {
      if (whole) return { from: 0, to: duration };
      const fits = now && seconds >= now.from && seconds <= now.to && (!track || (track.start >= now.from && track.stop <= now.to));
      return fits ? now : timelineWindow(track, seconds, duration);
    });
  }, [duration, loading, whole, seconds, track]);

  const at = useMemo(() => boxAt(track, still, seconds), [track, still, seconds]);

  const apply = (next: EditorTrack | null) => {
    setHistory((past) => [...past, current.current.track]);
    setTrack(next);
  };
  const seek = (value: number) => {
    pause();
    setSeconds(snapTenth(Math.min(current.current.duration ?? value, Math.max(0, value))));
  };
  const select = (index: number | null) => {
    pause();
    setSelected(index);
  };

  const commitBox = (box: Box) => {
    pause();
    const second = snapTenth(current.current.seconds);
    const next = setKeyframe(current.current.track, second, box, current.current.duration);
    if (!current.current.track) toast.info(NEW_TRACK_HINT);
    apply(next);
    setSeconds(second);
    setSelected(keyframeIndexAt(next, second));
  };
  const addKeyframeHere = () => {
    const here = boxAt(current.current.track, still, current.current.seconds);
    if (!here.box || here.state === 'keyframe' || !current.current.track) return;
    commitBox(here.box);
  };
  const deleteAt = (index: number) => {
    const live = current.current.track;
    if (!live) return;
    apply(deleteKeyframe(live, index));
    setSelected(null);
  };
  const setEnd = (which: 'start' | 'stop') => {
    const live = current.current.track;
    if (!live) return;
    const second = snapTenth(current.current.seconds);
    const next = which === 'start' ? setStart(live, second) : setStop(live, second, current.current.duration);
    if (next.start === live.start && next.stop === live.stop) return;
    apply(next);
  };
  const undo = () => {
    pause();
    setHistory((past) => {
      if (!past.length) return past;
      setTrack(past[past.length - 1]);
      return past.slice(0, -1);
    });
    setSelected(null);
  };

  const edits = history.length;
  const canDone = track !== null && edits > 0;
  const done = () => {
    if (!track || !canDone) return;
    const dense = densify(track);
    onDone({ source: 'reviewer', samples: dense, span: [dense[0].t, dense[dense.length - 1].t], partial: false, lostAt: null });
  };

  // Keyboard: the shortcuts in the help list. Typing in a field and activating a focused button are left alone.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest('input, textarea, select, [contenteditable]')) return;
      const live = current.current;
      const onHandle = !!target?.closest('[data-testid="track-keyframe"], [data-testid^="track-grip"]');
      const onButton = !!target?.closest('button') && !onHandle;
      const stepBy = event.shiftKey ? 1 : SNAP_SECONDS;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
        event.preventDefault();
        return undo();
      }
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      switch (event.key) {
        case ' ':
          if (onButton) return;
          event.preventDefault();
          return live.playing ? pause() : live.duration !== null && setPlaying(true);
        case 'ArrowLeft':
        case 'ArrowRight':
          if (onHandle) return;
          event.preventDefault();
          return seek(live.seconds + (event.key === 'ArrowLeft' ? -stepBy : stepBy));
        case 'k':
        case 'K':
          return addKeyframeHere();
        case 'i':
        case 'I':
          return setEnd('start');
        case 'o':
        case 'O':
          return setEnd('stop');
        case 'Delete':
        case 'Backspace':
          if (live.selected !== null) {
            event.preventDefault();
            deleteAt(live.selected);
          }
          return;
        case '?':
          event.preventDefault();
          return setHelp((open) => !open);
        default:
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const empty = track
    ? null
    : loadError
      ? loadError
      : still
        ? 'No track yet: drag the saved box to start a track from it, or draw a new box'
        : 'Draw a box to start a track';

  return (
    <Dialog
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      open
    >
      <DialogContent
        aria-label={`Edit track for ${name}`}
        className="max-h-[calc(100vh-2rem)] w-full max-w-[calc(100%-2rem)] gap-0 overflow-y-auto rounded-2xl bg-white p-0 shadow-xl sm:max-w-4xl"
        onOpenAutoFocus={(event) => event.preventDefault()}
        onEscapeKeyDown={(event) => {
          event.preventDefault();
          if (help) setHelp(false);
          else if (current.current.selected !== null) setSelected(null);
          else onClose();
        }}
        role="dialog"
      >
        <DialogHeader className="relative gap-2 border-b border-border/70 bg-muted/40 px-6 py-5 pr-24 text-left">
          <DialogTitle className="text-lg font-bold tracking-tight text-foreground">Edit track</DialogTitle>
          <p className="truncate text-sm font-semibold text-foreground" data-testid="track-item-title">
            {itemId && <span className="font-mono">{itemId}</span>}
            {itemId && name && ' · '}
            {name}
          </p>
          <fieldset aria-label="Track details" className="flex flex-wrap items-center gap-1.5 border-0 p-0">
            <Badge className="text-[10px]" variant="outline">
              {KIND_LABEL[kind]}
            </Badge>
            <Badge className="border-moss/30 bg-moss/10 text-[10px] text-moss" data-testid="track-source-badge" variant="outline">
              {badge}
            </Badge>
          </fieldset>
          <div className="absolute top-4 right-14">
            <Button
              aria-expanded={help}
              aria-label="Keyboard shortcuts"
              className="text-ink-caption"
              onClick={() => setHelp((open) => !open)}
              size="icon-sm"
              type="button"
              variant="ghost"
            >
              <CircleHelpIcon />
            </Button>
            {help && (
              <dl
                className="absolute top-full right-0 z-20 mt-1 w-72 space-y-1 rounded-xl bg-ink p-3 text-[11px] text-white shadow-xl"
                data-testid="track-help"
              >
                {SHORTCUTS.map(([key, text]) => (
                  <div className="flex items-baseline justify-between gap-3" key={key}>
                    <dt className="rounded bg-white/15 px-1.5 py-0.5 font-mono text-[10.5px]">{key}</dt>
                    <dd className="text-right text-white/80">{text}</dd>
                  </div>
                ))}
              </dl>
            )}
          </div>
        </DialogHeader>
        <div className="flex flex-col gap-3 p-5">
          <DialogDescription className="flex flex-col gap-1 text-xs leading-relaxed text-muted-foreground">
            <span>Move the box at the playhead to add a keyframe. It transitions smoothly between keyframes.</span>
            <span>Use the timeline’s end handles to set when {name || 'the item'} is visible.</span>
          </DialogDescription>

          <TrackFrame at={at} busy={loading} duration={duration} empty={empty} onBox={commitBox} seconds={seconds}>
            <MutedVideo
              className="h-full w-full object-fill"
              onLoadedMetadata={(event) => {
                const length = event.currentTarget.duration;
                if (Number.isFinite(length) && length > 0) setDuration(length);
              }}
              playsInline
              preload="auto"
              ref={videoRef}
              src={videoSrc}
            />
          </TrackFrame>

          <div className="flex flex-wrap items-center gap-1.5">
            <Button
              aria-label={playing ? 'Pause' : 'Play'}
              disabled={duration === null}
              onClick={() => (playing ? pause() : setPlaying(true))}
              size="icon-sm"
              type="button"
              variant="ghost"
            >
              {playing ? <PauseIcon /> : <PlayIcon />}
            </Button>
            <Button aria-label="Back 0.1 s" onClick={() => seek(seconds - SNAP_SECONDS)} size="icon-sm" type="button" variant="ghost">
              <ChevronLeftIcon />
            </Button>
            <Button aria-label="Forward 0.1 s" onClick={() => seek(seconds + SNAP_SECONDS)} size="icon-sm" type="button" variant="ghost">
              <ChevronRightIcon />
            </Button>
            <span className="font-mono text-xs text-ink" data-testid="track-clock">
              {formatEditorClock(seconds)}
              <span className="text-ink-caption"> / {formatEditorClock(duration ?? 0)}</span>
            </span>
            {duration !== null && duration > 20 && (
              <Button
                className="h-6 rounded-full px-2 text-[11px] text-ink hover:bg-ink/5"
                onClick={() => setWhole((value) => !value)}
                size="xs"
                type="button"
                variant="ghost"
              >
                {whole ? 'Zoom to track' : 'Whole clip'}
              </Button>
            )}
            <span className="ml-auto flex items-center gap-3 text-[11px] text-ink-caption">
              <span className="flex items-center gap-1">
                <span className="size-2.5 rotate-45 rounded-[2px] border border-white bg-moss shadow" />
                keyframe
              </span>
              <span className="flex items-center gap-1">
                <span className="h-2.5 w-4 rounded-sm border border-moss/40 bg-moss/15" />
                on screen
              </span>
            </span>
          </div>

          {view && duration !== null ? (
            <TrackTimeline
              duration={duration}
              onBegin={() => setHistory((past) => [...past, current.current.track])}
              onChange={setTrack}
              onDelete={deleteAt}
              onSeek={seek}
              onSelect={select}
              seconds={seconds}
              selected={selected}
              track={track}
              view={view}
            />
          ) : (
            <p className="py-6 text-center text-xs text-muted-foreground" data-testid="track-timeline-loading">
              {loadError ? 'The video could not be read.' : 'Loading the video…'}
            </p>
          )}

          <DialogFooter className="flex flex-row items-center justify-between gap-2 text-sm sm:justify-between">
            <p className="text-[11px] text-muted-foreground" data-testid="track-edit-count">
              {edits} {edits === 1 ? 'edit' : 'edits'} · goes into the draft on Done
            </p>
            <div className="flex items-center gap-2">
              <Button disabled={!edits} onClick={undo} size="sm" type="button" variant="ghost">
                Undo
              </Button>
              <Button
                className="rounded-full border border-ink/20 font-semibold"
                onClick={onClose}
                size="sm"
                type="button"
                variant="outline"
              >
                Cancel
              </Button>
              <Button
                className="rounded-full bg-ink font-semibold text-white hover:bg-ink/90 disabled:opacity-40"
                disabled={!canDone}
                onClick={done}
                size="sm"
                type="button"
              >
                Done
              </Button>
            </div>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  );
}

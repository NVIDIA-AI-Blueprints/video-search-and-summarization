// SPDX-License-Identifier: Apache-2.0

'use client';

import { useRef } from 'react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { formatClock } from '@/modules/reports/domain/normalize';
import {
  DELIBERATE_DRAG_SECONDS,
  type EditorTrack,
  formatEditorClock,
  isDimmed,
  moveKeyframe,
  rulerTicks,
  SNAP_SECONDS,
  setStart,
  setStop,
  snapTenth,
} from '@/modules/reports/domain/track-editor';

type Drag =
  | { kind: 'scrub' }
  | { kind: 'grip'; which: 'start' | 'stop'; started: boolean; offset: number }
  | { kind: 'key'; index: number; origin: number; started: boolean };

const KEY_STEP = (shift: boolean) => (shift ? 1 : SNAP_SECONDS);

/**
 * The timeline under the frame: a ruler, a lane with the track's span, its keyframes (diamonds joined by a line) and the
 * start and stop grips, and the playhead. It shows `view` (a window of the clip). Clicking or dragging the ruler or lane
 * scrubs; dragging a diamond moves that keyframe in time; dragging a grip moves the start or stop. Diamonds and grips are
 * buttons, so the arrow keys move them too.
 */
export function TrackTimeline({
  track,
  duration,
  view,
  seconds,
  selected,
  onSeek,
  onSelect,
  onDelete,
  onBegin,
  onChange,
}: {
  track: EditorTrack | null;
  duration: number;
  view: { from: number; to: number };
  seconds: number;
  selected: number | null;
  onSeek: (seconds: number) => void;
  onSelect: (index: number | null) => void;
  onDelete: (index: number) => void;
  /** Called once before a drag first changes the track: the editor takes its undo snapshot here. */
  onBegin: () => void;
  onChange: (next: EditorTrack) => void;
}) {
  const laneRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<Drag | null>(null);
  const span = view.to - view.from;
  const left = (t: number) => `${+(((t - view.from) / span) * 100).toFixed(3)}%`;
  const timeAt = (clientX: number) => {
    const rect = laneRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return 0;
    return snapTenth(Math.min(duration, Math.max(0, view.from + ((clientX - rect.left) / rect.width) * span)));
  };

  const move = (event: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    const t = timeAt(event.clientX);
    if (drag.kind === 'scrub') return onSeek(t);
    if (!track) return;
    if (drag.kind === 'grip') {
      const edge = t - drag.offset;
      const next = drag.which === 'start' ? setStart(track, edge) : setStop(track, edge, duration);
      const current = drag.which === 'start' ? track.start : track.stop;
      const target = drag.which === 'start' ? next.start : next.stop;
      if (target === current) return;
      if (!drag.started) {
        drag.started = true;
        onBegin();
      }
      onChange(next);
      return onSeek(target);
    }
    if (!drag.started && Math.abs(t - drag.origin) < DELIBERATE_DRAG_SECONDS) return;
    const next = moveKeyframe(track, drag.index, t);
    if (next === track || next.kfs[drag.index].t === track.kfs[drag.index].t) return;
    if (!drag.started) {
      drag.started = true;
      onBegin();
    }
    onChange(next);
    onSeek(next.kfs[drag.index].t);
  };
  const release = (event: React.PointerEvent) => {
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const begin = (drag: Drag) => (event: React.PointerEvent<HTMLElement>) => {
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    if (drag.kind === 'grip' && track) drag.offset = timeAt(event.clientX) - (drag.which === 'start' ? track.start : track.stop);
    dragRef.current = drag;
  };

  const nudgeKey = (index: number, event: React.KeyboardEvent) => {
    if (!track) return;
    const delta = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0;
    if (!delta) return;
    event.preventDefault();
    event.stopPropagation();
    const next = moveKeyframe(track, index, track.kfs[index].t + delta * KEY_STEP(event.shiftKey));
    if (next === track || next.kfs[index].t === track.kfs[index].t) return;
    onBegin();
    onChange(next);
    onSeek(next.kfs[index].t);
  };
  const nudgeGrip = (which: 'start' | 'stop', event: React.KeyboardEvent) => {
    if (!track) return;
    const delta = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0;
    if (!delta) return;
    event.preventDefault();
    event.stopPropagation();
    const step = delta * KEY_STEP(event.shiftKey);
    const next = which === 'start' ? setStart(track, track.start + step) : setStop(track, track.stop + step, duration);
    if ((which === 'start' ? next.start : next.stop) === (which === 'start' ? track.start : track.stop)) return;
    onBegin();
    onChange(next);
    onSeek(which === 'start' ? next.start : next.stop);
  };

  const ticks = rulerTicks(view.from, view.to);
  const visible = (t: number) => t >= view.from - 1e-6 && t <= view.to + 1e-6;
  const picked = track && selected !== null ? track.kfs[selected] : null;

  return (
    <div className="relative select-none" data-testid="track-timeline">
      {picked && visible(picked.t) && (
        <div
          className="absolute bottom-full z-10 mb-1 flex -translate-x-1/2 items-center gap-2 rounded-xl bg-ink px-2.5 py-1.5 text-[11px] text-white shadow-lg"
          data-testid="track-keyframe-popover"
          style={{ left: `clamp(7rem, ${left(picked.t)}, calc(100% - 7rem))` }}
        >
          <span className="font-mono">Keyframe {formatEditorClock(picked.t)}</span>
          <Button
            className="h-5 rounded-full bg-clay px-2 text-[11px] font-semibold text-white hover:bg-clay/90"
            data-testid="track-keyframe-delete"
            onClick={() => selected !== null && onDelete(selected)}
            size="xs"
            type="button"
          >
            {track?.kfs.length === 1 ? 'Remove track' : 'Delete'}
          </Button>
        </div>
      )}
      <div
        className="relative h-6 cursor-pointer border-b border-ink/15"
        data-testid="track-ruler"
        onPointerCancel={release}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          dragRef.current = { kind: 'scrub' };
          onSelect(null);
          onSeek(timeAt(event.clientX));
        }}
        onPointerMove={move}
        onPointerUp={release}
      >
        {ticks.map((tick) => (
          <span className="pointer-events-none absolute bottom-0 -translate-x-1/2" key={tick.t} style={{ left: left(tick.t) }}>
            {tick.label && <span className="mb-0.5 block font-mono text-[9.5px] text-ink-caption">{formatClock(tick.t)}</span>}
            <span className={cn('mx-auto block w-px bg-ink/30', tick.label ? 'h-1.5' : 'h-1')} />
          </span>
        ))}
      </div>
      <div
        className="relative mb-5 h-16 cursor-pointer rounded-b-xl bg-muted/60"
        data-testid="track-lane"
        onPointerCancel={release}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          dragRef.current = { kind: 'scrub' };
          onSelect(null);
          onSeek(timeAt(event.clientX));
        }}
        onPointerMove={move}
        onPointerUp={release}
        ref={laneRef}
      >
        {track && (
          <>
            <div
              className="pointer-events-none absolute top-3 bottom-3 rounded-md border border-moss/40 bg-moss/15"
              data-testid="track-span"
              style={{ left: left(Math.max(track.start, view.from)), right: `calc(100% - ${left(Math.min(track.stop, view.to))})` }}
            />
            {track.kfs.length > 1 && (
              <div
                className="pointer-events-none absolute top-1/2 h-0.5 -translate-y-1/2 bg-moss/70"
                style={{
                  left: left(Math.max(track.kfs[0].t, view.from)),
                  right: `calc(100% - ${left(Math.min(track.kfs[track.kfs.length - 1].t, view.to))})`,
                }}
              />
            )}
            {track.kfs.map((kf, index) =>
              visible(kf.t) ? (
                <button
                  aria-label={`Keyframe at ${formatEditorClock(kf.t)}`}
                  aria-pressed={selected === index}
                  className={cn(
                    'absolute top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rotate-45 cursor-grab touch-none rounded-[3px] border-2 border-white bg-moss shadow outline-none transition-colors focus-visible:ring-2 focus-visible:ring-signal active:cursor-grabbing',
                    selected === index && 'bg-signal ring-2 ring-ink',
                    isDimmed(track, kf) && 'opacity-40',
                  )}
                  data-testid="track-keyframe"
                  // biome-ignore lint/suspicious/noArrayIndexKey: the index is the keyframe's identity; a time key would remount the button mid-drag and drop the pointer capture
                  key={index}
                  onKeyDown={(event) => nudgeKey(index, event)}
                  onPointerCancel={release}
                  onPointerDown={(event) => {
                    begin({ kind: 'key', index, origin: kf.t, started: false })(event);
                    onSelect(index);
                    onSeek(kf.t);
                  }}
                  onPointerMove={move}
                  onPointerUp={release}
                  style={{ left: left(kf.t) }}
                  type="button"
                />
              ) : null,
            )}
            {(['start', 'stop'] as const).map((which) => {
              const t = which === 'start' ? track.start : track.stop;
              return visible(t) ? (
                <button
                  aria-label={`${which === 'start' ? 'Start' : 'Stop'} of the track, ${formatEditorClock(t)}`}
                  className={cn(
                    'absolute top-1.5 bottom-1.5 flex w-3 cursor-ew-resize touch-none items-center justify-center rounded-sm bg-ink outline-none focus-visible:ring-2 focus-visible:ring-signal',
                    which === 'start' ? '-ml-2 -translate-x-full' : 'ml-2',
                  )}
                  data-testid={`track-grip-${which}`}
                  key={which}
                  onKeyDown={(event) => nudgeGrip(which, event)}
                  onPointerCancel={release}
                  onPointerDown={begin({ kind: 'grip', which, started: false, offset: 0 })}
                  onPointerMove={move}
                  onPointerUp={release}
                  style={{ left: left(t) }}
                  type="button"
                >
                  <span className="h-4 w-0.5 rounded-full bg-white/70" />
                  <span
                    className={cn(
                      'pointer-events-none absolute top-full mt-0.5 whitespace-nowrap font-mono text-[9.5px] font-semibold text-ink',
                      which === 'start' ? 'right-0' : 'left-0',
                    )}
                  >
                    {formatEditorClock(t)}
                  </span>
                </button>
              ) : null;
            })}
          </>
        )}
        {visible(seconds) && (
          <div
            className="pointer-events-none absolute -top-6 bottom-0 z-[1] w-0.5 -translate-x-1/2 bg-clay"
            data-testid="track-playhead"
            style={{ left: left(seconds) }}
          >
            <span className="absolute -top-0.5 left-1/2 size-2 -translate-x-1/2 rotate-45 bg-clay" />
          </div>
        )}
      </div>
    </div>
  );
}

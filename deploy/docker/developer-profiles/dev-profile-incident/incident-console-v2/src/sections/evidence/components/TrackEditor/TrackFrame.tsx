// SPDX-License-Identifier: Apache-2.0

'use client';

import { useRef, useState } from 'react';

import { cn } from '@/lib/utils';
import {
  type Box,
  type BoxAt,
  type BoxHandle,
  boxBetween,
  dragBox,
  drawsBox,
  formatEditorClock,
  isUsableBox,
  STATE_CHIP,
} from '@/modules/reports/domain/track-editor';

const HANDLES: Array<{ handle: BoxHandle; style: React.CSSProperties; cursor: string }> = [
  { handle: 'nw', style: { left: 0, top: 0 }, cursor: 'nwse-resize' },
  { handle: 'n', style: { left: '50%', top: 0 }, cursor: 'ns-resize' },
  { handle: 'ne', style: { left: '100%', top: 0 }, cursor: 'nesw-resize' },
  { handle: 'e', style: { left: '100%', top: '50%' }, cursor: 'ew-resize' },
  { handle: 'se', style: { left: '100%', top: '100%' }, cursor: 'nwse-resize' },
  { handle: 's', style: { left: '50%', top: '100%' }, cursor: 'ns-resize' },
  { handle: 'sw', style: { left: 0, top: '100%' }, cursor: 'nesw-resize' },
  { handle: 'w', style: { left: 0, top: '50%' }, cursor: 'ew-resize' },
];

type Gesture = { kind: 'edit'; handle: BoxHandle; start: Box; from: [number, number] } | { kind: 'draw'; from: [number, number] };

const percent = (value: number) => `${+(value / 10).toFixed(3)}%`;

/**
 * The frame the box is edited on: the video (children) with the box over it. Dragging the box moves it, dragging a handle
 * resizes it, and where there is no box dragging draws one. Either hands the box back once, on release; the editor turns it
 * into a keyframe at the playhead. Boxes are 0-1000 [ymin, xmin, ymax, xmax] over the whole frame (the video is object-fill).
 */
export function TrackFrame({
  at,
  seconds,
  duration,
  busy,
  empty,
  onBox,
  children,
}: {
  at: BoxAt;
  seconds: number;
  duration: number | null;
  /** True while the saved track is still loading: nothing can be drawn yet. */
  busy: boolean;
  /** The message to show over the frame when there is no track, or null. */
  empty: string | null;
  onBox: (box: Box) => void;
  children: React.ReactNode;
}) {
  const frameRef = useRef<HTMLDivElement>(null);
  const gestureRef = useRef<Gesture | null>(null);
  // The box (or drawn rectangle) shown while a drag is in progress.
  const [live, setLive] = useState<Box | null>(null);

  const pointOf = (event: React.PointerEvent): [number, number] => {
    const rect = (frameRef.current as HTMLDivElement).getBoundingClientRect();
    return [((event.clientX - rect.left) / rect.width) * 1000, ((event.clientY - rect.top) / rect.height) * 1000];
  };
  const track = (event: React.PointerEvent) => {
    const gesture = gestureRef.current;
    if (!gesture) return;
    const [x, y] = pointOf(event);
    if (gesture.kind === 'draw') setLive(boxBetween(gesture.from, [x, y]));
    else setLive(dragBox(gesture.start, gesture.handle, x - gesture.from[0], y - gesture.from[1]));
  };
  const finish = (event: React.PointerEvent, commit: boolean) => {
    const gesture = gestureRef.current;
    gestureRef.current = null;
    const box = live;
    setLive(null);
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    if (!commit || !gesture || !box) return;
    if (gesture.kind === 'draw' ? isUsableBox(box) : box.some((value, i) => value !== (gesture.start as Box)[i])) onBox(box);
  };
  const start = (event: React.PointerEvent<HTMLElement>, gesture: (point: [number, number]) => Gesture) => {
    if (busy || event.button > 0) return;
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    gestureRef.current = gesture(pointOf(event));
  };

  const shown = live ?? at.box;
  const dashed = at.state === 'held' || at.state === 'still';
  const drawing = drawsBox(at.state);
  // A saved still box can be dragged like a keyframe's box, though a drag elsewhere draws.
  const movable = !drawing || at.state === 'still';
  const drawnNow = live !== null && gestureRef.current?.kind === 'draw';

  return (
    <div
      className={cn('relative aspect-video select-none overflow-hidden rounded-xl bg-black', drawing && !busy && 'cursor-crosshair')}
      data-box-state={at.state}
      data-testid="track-frame"
      onPointerCancel={(event) => finish(event, false)}
      onPointerDown={(event) => {
        if (drawing) start(event, (from) => ({ kind: 'draw', from }));
      }}
      onPointerMove={track}
      onPointerUp={(event) => finish(event, true)}
      ref={frameRef}
    >
      {children}
      {at.state === 'outside' && <div className="pointer-events-none absolute inset-0 bg-black/50" data-testid="track-veil" />}
      {shown && (
        <div
          className={cn(
            'absolute border-2 shadow-[0_0_0_1px_rgba(0,0,0,0.6)]',
            drawnNow ? 'border-white bg-white/15' : 'border-signal bg-signal/10',
            dashed && !live && 'border-dashed',
            movable ? 'cursor-move touch-none' : 'pointer-events-none',
          )}
          data-testid="track-box"
          onPointerDown={(event) => {
            if (movable) start(event, (from) => ({ kind: 'edit', handle: 'move', start: shown, from }));
          }}
          style={{
            left: percent(shown[1]),
            top: percent(shown[0]),
            width: percent(shown[3] - shown[1]),
            height: percent(shown[2] - shown[0]),
          }}
        >
          {movable &&
            !drawnNow &&
            HANDLES.map(({ handle, style, cursor }) => (
              <span
                className="absolute size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-[2px] border border-ink bg-white"
                data-handle={handle}
                key={handle}
                onPointerDown={(event) => start(event, (from) => ({ kind: 'edit', handle, start: shown, from }))}
                style={{ ...style, cursor, touchAction: 'none' }}
              />
            ))}
        </div>
      )}
      {empty && !shown && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-6 text-center">
          <p className="rounded-xl bg-black/65 px-4 py-2 text-xs font-semibold text-white" data-testid="track-empty">
            {empty}
          </p>
        </div>
      )}
      <p
        className="pointer-events-none absolute bottom-2 left-2 max-w-[75%] rounded-full bg-black/70 px-2.5 py-1 text-[11px] font-semibold text-white"
        data-testid="track-chip"
      >
        {busy ? 'Loading the track…' : STATE_CHIP[at.state]}
      </p>
      <p className="pointer-events-none absolute right-2 bottom-2 rounded-full bg-black/70 px-2.5 py-1 font-mono text-[11px] text-white">
        {formatEditorClock(seconds)}
        {duration !== null ? ` / ${formatEditorClock(duration)}` : ''}
      </p>
    </div>
  );
}

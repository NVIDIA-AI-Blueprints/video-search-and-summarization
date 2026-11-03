// SPDX-License-Identifier: Apache-2.0

'use client';

import { useRef, useState } from 'react';

import { boxRegion } from '@/lib/evidence/frame-geometry';
import { dragRegion, type FramePoint, regionBox } from '@/lib/evidence/frame-picker';
import type { Box } from '@/modules/reports/domain/reviewer-item';

const percent = (fraction: number) => `${+(fraction * 100).toFixed(3)}%`;

/**
 * A 16:9 picture (the children) with a surface over it. When `drawable`, dragging draws a rectangle and hands it back as a
 * 0-1000 box; the surface covers the picture exactly (object-fill), so a point maps to the same fraction of the frame.
 * `box` is the box shown when nothing is being dragged.
 */
export function BoxCanvas({
  box,
  drawable,
  label,
  onDraw,
  testId,
  children,
}: {
  box: readonly number[] | null;
  drawable: boolean;
  label?: string;
  onDraw?: (box: Box) => void;
  testId: string;
  children: React.ReactNode;
}) {
  const originRef = useRef<FramePoint | null>(null);
  const [drag, setDrag] = useState<{ from: FramePoint; to: FramePoint } | null>(null);
  const drawn = drag ? dragRegion(drag.from, drag.to) : null;
  const region = drawn ?? boxRegion(box);

  const pointOn = (event: React.PointerEvent<HTMLDivElement>): FramePoint => {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: (event.clientX - rect.left) / rect.width, y: (event.clientY - rect.top) / rect.height };
  };
  const release = (event: React.PointerEvent<HTMLDivElement>) => {
    originRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  return (
    <div className="relative aspect-video select-none overflow-hidden rounded-xl bg-black" data-testid={testId}>
      {children}
      {drawable && (
        <div
          className="absolute inset-0 cursor-crosshair touch-none"
          data-testid={`${testId}-surface`}
          onPointerCancel={(event) => {
            setDrag(null);
            release(event);
          }}
          onPointerDown={(event) => {
            event.currentTarget.setPointerCapture(event.pointerId);
            const point = pointOn(event);
            originRef.current = point;
            setDrag({ from: point, to: point });
          }}
          onPointerMove={(event) => {
            const from = originRef.current;
            if (from) setDrag({ from, to: pointOn(event) });
          }}
          onPointerUp={(event) => {
            const from = originRef.current;
            const finished = from ? dragRegion(from, pointOn(event)) : null;
            setDrag(null);
            release(event);
            if (finished) onDraw?.(regionBox(finished) as Box);
          }}
        />
      )}
      {region && (
        <div
          className="pointer-events-none absolute border-2 border-white bg-white/15 shadow-[0_0_0_1px_rgba(0,0,0,0.6)]"
          data-testid={`${testId}-box`}
          style={{ left: percent(region.x), top: percent(region.y), width: percent(region.w), height: percent(region.h) }}
        >
          {label && !drawn && (
            <span className="absolute -top-5 left-0 max-w-full truncate rounded bg-white px-1.5 text-[10px] font-semibold text-ink">
              {label}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

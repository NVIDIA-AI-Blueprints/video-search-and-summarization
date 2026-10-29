// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React, { useState } from 'react';
import { Tooltip } from 'common/warehouse/mui';

import Styles from './DashVideo.module.css';

export function parseReplayWindowMs(
  startTime?: string,
  endTime?: string,
): { startMs: number; endMs: number } | null {
  const startMs = Date.parse(startTime ?? '');
  const endMs = Date.parse(endTime ?? '');
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) {
    return null;
  }
  return { startMs, endMs };
}

/** Prefer the caller's timestring when it is the same instant; otherwise ISO. */
export function formatReplayTimestring(timeMs: number, source?: string) {
  if (source) {
    const parsed = Date.parse(source);
    if (Number.isFinite(parsed) && Math.abs(parsed - timeMs) < 1) {
      return source;
    }
  }
  if (!Number.isFinite(timeMs)) return '';
  return new Date(timeMs).toISOString();
}

function playheadFraction(startMs: number, endMs: number, currentMs: number) {
  const span = endMs - startMs;
  if (!(span > 0)) return 0;
  return Math.min(1, Math.max(0, (currentMs - startMs) / span));
}

/** Native range thumb width; the bubble layer is inset by half of it. */
const THUMB_PX = 16;

type HoverState = { fraction: number; overThumb: boolean };

export default function DashVideoTimeline({
  startMs,
  endMs,
  currentTimeMs,
  startTime,
  endTime,
  seekable,
  onSeekEnd,
}: {
  startMs: number;
  endMs: number;
  currentTimeMs: number;
  startTime?: string;
  endTime?: string;
  seekable: boolean;
  onSeekEnd?: (timeMs: number) => void;
}) {
  const [isControlled, setIsControlled] = useState(false);
  const [scrubMs, setScrubMs] = useState(currentTimeMs);
  const dragOriginMs = React.useRef<number | null>(null);
  const [hover, setHover] = useState<HoverState | null>(null);
  const displayMs = isControlled ? scrubMs : currentTimeMs;
  const startLabel = formatReplayTimestring(startMs, startTime);
  const endLabel = formatReplayTimestring(endMs, endTime);
  const labelAt = (ms: number) =>
    formatReplayTimestring(
      ms,
      Math.abs(ms - startMs) < 1
        ? startTime
        : Math.abs(ms - endMs) < 1
          ? endTime
          : undefined,
    );
  const currentLabel = labelAt(displayMs);
  const playhead = playheadFraction(startMs, endMs, displayMs);
  const hoverMs = hover ? startMs + hover.fraction * (endMs - startMs) : 0;
  const bubbleFraction = hover ? (hover.overThumb ? playhead : hover.fraction) : 0;

  const trackMouse = (event: React.MouseEvent<HTMLInputElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const usable = rect.width - THUMB_PX;
    if (!(usable > 0)) return;
    const x = event.clientX - rect.left;
    const fraction = Math.min(1, Math.max(0, (x - THUMB_PX / 2) / usable));
    const thumbX = THUMB_PX / 2 + playhead * usable;
    setHover({ fraction, overThumb: Math.abs(x - thumbX) <= THUMB_PX / 2 });
  };

  const commit = (raw: string) => {
    if (!seekable) return;
    const next = Number(raw);
    if (!Number.isFinite(next)) return;
    const origin = dragOriginMs.current;
    dragOriginMs.current = null;
    if (origin !== null && Math.abs(next - origin) < 400) {
      setIsControlled(false);
      return;
    }
    onSeekEnd?.(next);
    window.setTimeout(() => setIsControlled(false), 1000);
  };

  return (
    <div className={Styles.timelineContainer}>
      <Tooltip title={startLabel} placement="top">
        <button
          type="button"
          className={Styles.endMarker}
          aria-label={`Start ${startLabel}`}
        >
          <span className={Styles.endMarkerTick} />
        </button>
      </Tooltip>
      <div className={Styles.seekRow}>
        {hover ? (
          <div
            className={Styles.playheadLayer}
            style={{ ['--playhead' as string]: String(bubbleFraction) }}
          >
            <div
              className={Styles.playheadBubble}
              role="status"
              aria-live="polite"
              aria-label={
                hover.overThumb
                  ? `Current time ${currentLabel}`
                  : `Time at pointer ${labelAt(hoverMs)}`
              }
            >
              {hover.overThumb ? currentLabel : labelAt(hoverMs)}
            </div>
          </div>
        ) : null}
        <input
          type="range"
          min={startMs}
          max={endMs}
          step="any"
          value={displayMs}
          disabled={!seekable}
          aria-label="Seek recorded video"
          aria-valuetext={currentLabel}
          className={Styles.seekInput}
          onMouseMove={trackMouse}
          onMouseLeave={() => setHover(null)}
          onPointerDown={() => {
            if (!seekable) return;
            setIsControlled(true);
            setScrubMs(displayMs);
            dragOriginMs.current = displayMs;
          }}
          onChange={(event) => {
            if (!seekable) return;
            setScrubMs(Number(event.target.value));
          }}
          onPointerUp={(event) => commit(event.currentTarget.value)}
          onKeyUp={(event) => {
            if (
              event.key === 'ArrowLeft' ||
              event.key === 'ArrowRight' ||
              event.key === 'Home' ||
              event.key === 'End'
            ) {
              commit(event.currentTarget.value);
            }
          }}
        />
      </div>
      <Tooltip title={endLabel} placement="top">
        <button
          type="button"
          className={Styles.endMarker}
          aria-label={`End ${endLabel}`}
        >
          <span className={Styles.endMarkerTick} />
        </button>
      </Tooltip>
    </div>
  );
}

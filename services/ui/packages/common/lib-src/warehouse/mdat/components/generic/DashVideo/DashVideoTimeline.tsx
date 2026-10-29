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

function formatReplayTime(timeMs: number) {
  return new Date(timeMs).toLocaleString();
}

export default function DashVideoTimeline({
  startMs,
  endMs,
  currentTimeMs,
  seekable,
  onSeekEnd,
}: {
  startMs: number;
  endMs: number;
  currentTimeMs: number;
  seekable: boolean;
  onSeekEnd?: (timeMs: number) => void;
}) {
  const [isControlled, setIsControlled] = useState(false);
  const [scrubMs, setScrubMs] = useState(currentTimeMs);
  const dragOriginMs = React.useRef<number | null>(null);
  const displayMs = isControlled ? scrubMs : currentTimeMs;
  const label = formatReplayTime(displayMs);

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
      <span className={Styles.timeLabel}>{label}</span>
      <Tooltip title={label} placement="top">
        <input
          type="range"
          min={startMs}
          max={endMs}
          step="any"
          value={displayMs}
          disabled={!seekable}
          aria-label="Seek recorded video"
          className={Styles.seekInput}
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
      </Tooltip>
    </div>
  );
}

// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Recent still frame for a registered VST sensor. Resolves `sensorName` to a
 * VST stream id via `/v1/sensor/list` (cached per `vstApiUrl`), then renders
 * `/v1/replay/stream/{id}/picture` within its latest recorded segment as an `<img>`. The
 * replay endpoint is used instead of `/v1/live/...` to avoid hitting the live
 * pipeline for what is effectively a preview.
 */

import React, { useEffect, useState } from 'react';
import { IconCamera, IconAlertTriangle, IconLoader2 } from '@tabler/icons-react';
import { fetchSensorMap } from '../utils/vstSensorList';

export { clearSensorListCache } from '../utils/vstSensorList';

/** Resolved replay-picture URLs per VST base + sensor name (survives remounts). */
const pictureUrlCache = new Map<string, string>();

const pictureCacheKey = (vstApiUrl: string, sensorName: string) =>
  `${vstApiUrl.replace(/\/+$/, '')}|${sensorName}`;

export const clearVstStreamThumbnailCache = (vstApiUrl?: string, sensorName?: string): void => {
  if (!vstApiUrl && !sensorName) {
    pictureUrlCache.clear();
    return;
  }
  if (vstApiUrl && sensorName) {
    pictureUrlCache.delete(pictureCacheKey(vstApiUrl, sensorName));
    return;
  }
  const prefix = vstApiUrl ? `${vstApiUrl.replace(/\/+$/, '')}|` : undefined;
  for (const key of pictureUrlCache.keys()) {
    if (prefix && key.startsWith(prefix)) {
      pictureUrlCache.delete(key);
    } else if (sensorName && key.endsWith(`|${sensorName}`)) {
      pictureUrlCache.delete(key);
    }
  }
};

interface VstStreamThumbnailProps {
  vstApiUrl?: string;
  /** Friendly sensor name as registered with VST (`name` in `/v1/sensor/list`). */
  sensorName: string;
  isDark: boolean;
  fallbackLabel?: string;
}

const THUMBNAIL_BOX_STYLE: React.CSSProperties = { width: '128px', height: '72px' };

// Look back from the recorded segment end, rather than the wall clock: uploaded
// files and disconnected cameras can have historical timelines. Short segments
// use their midpoint so the snapshot stays inside the recording.
const THUMBNAIL_LOOKBACK_MS = 5_000;

interface RecordedSegment {
  startTime: string;
  endTime: string;
}

const fetchPreviewTime = async (baseUrl: string, sensorId: string): Promise<string> => {
  const response = await fetch(
    `${baseUrl}/v1/storage/${encodeURIComponent(sensorId)}/timelines`,
  );
  if (!response.ok) {
    throw new Error(`VST timelines returned ${response.status}`);
  }
  const data: unknown = await response.json();
  let latest: { start: number; end: number } | undefined;
  if (Array.isArray(data)) {
    for (const segment of data as RecordedSegment[]) {
      if (typeof segment?.startTime !== 'string' || typeof segment?.endTime !== 'string') {
        continue;
      }
      const start = Date.parse(segment.startTime);
      const end = Date.parse(segment.endTime);
      if (Number.isFinite(start) && Number.isFinite(end) && end > start &&
          (!latest || end > latest.end)) {
        latest = { start, end };
      }
    }
  }
  if (!latest) {
    throw new Error('No recorded timeline available');
  }
  const lookback = Math.min(THUMBNAIL_LOOKBACK_MS, (latest.end - latest.start) / 2);
  return new Date(latest.end - lookback).toISOString();
};

const Placeholder: React.FC<{
  isDark: boolean;
  state: 'idle' | 'loading' | 'unavailable' | 'no-name';
  label?: string;
}> = ({ isDark, state, label }) => {
  const baseClass = `flex flex-col items-center justify-center rounded border text-xs gap-1 ${
    isDark
      ? 'border-neutral-700 bg-neutral-900 text-neutral-500'
      : 'border-gray-300 bg-gray-50 text-gray-500'
  }`;

  const renderIcon = () => {
    switch (state) {
      case 'loading':
        return <IconLoader2 className="w-5 h-5 animate-spin" />;
      case 'unavailable':
        return <IconAlertTriangle className="w-5 h-5" />;
      default:
        return <IconCamera className="w-6 h-6" />;
    }
  };

  const text = label
    ? label
    : state === 'unavailable'
    ? 'No thumbnail'
    : state === 'no-name'
    ? 'Thumbnail'
    : '';

  return (
    <div data-testid="vst-stream-thumbnail-placeholder" style={THUMBNAIL_BOX_STYLE} className={baseClass}>
      {renderIcon()}
      {text && <span className="px-1 truncate max-w-full">{text}</span>}
    </div>
  );
};

type ThumbnailState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; pictureUrl: string }
  | { kind: 'unavailable'; reason: string };

const initialThumbnailState = (
  vstApiUrl: string | undefined,
  sensorName: string,
): ThumbnailState => {
  if (!sensorName) {
    return { kind: 'idle' };
  }
  if (!vstApiUrl) {
    return { kind: 'unavailable', reason: 'VST URL not configured' };
  }
  const cachedUrl = pictureUrlCache.get(pictureCacheKey(vstApiUrl, sensorName));
  if (cachedUrl) {
    return { kind: 'ready', pictureUrl: cachedUrl };
  }
  return { kind: 'loading' };
};

export const VstStreamThumbnail: React.FC<VstStreamThumbnailProps> = ({
  vstApiUrl,
  sensorName,
  isDark,
  fallbackLabel,
}) => {
  const [state, setState] = useState<ThumbnailState>(() =>
    initialThumbnailState(vstApiUrl, sensorName),
  );
  /** URL that failed to load; cleared implicitly when `pictureUrl` changes. */
  const [brokenPictureUrl, setBrokenPictureUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!sensorName) {
      setState({ kind: 'idle' });
      return;
    }
    if (!vstApiUrl) {
      setState({ kind: 'unavailable', reason: 'VST URL not configured' });
      return;
    }

    const cacheKey = pictureCacheKey(vstApiUrl, sensorName);
    const cachedUrl = pictureUrlCache.get(cacheKey);
    if (cachedUrl) {
      setState({ kind: 'ready', pictureUrl: cachedUrl });
    } else {
      setState({ kind: 'loading' });
    }

    let cancelled = false;

    fetchSensorMap(vstApiUrl)
      .then(async (map) => {
        if (cancelled) return;
        const sensorId = map.get(sensorName);
        if (!sensorId) {
          pictureUrlCache.delete(cacheKey);
          setState({
            kind: 'unavailable',
            reason: `Sensor "${sensorName}" not registered with VST`,
          });
          return;
        }
        let baseUrl = vstApiUrl;
        while (baseUrl.endsWith('/')) baseUrl = baseUrl.slice(0, -1);
        const startTime = await fetchPreviewTime(baseUrl, sensorId);
        if (cancelled) return;
        const pictureUrl = `${baseUrl}/v1/replay/stream/${encodeURIComponent(
          sensorId,
        )}/picture?startTime=${encodeURIComponent(startTime)}`;
        pictureUrlCache.set(cacheKey, pictureUrl);
        setState({ kind: 'ready', pictureUrl });
      })
      .catch((err) => {
        if (cancelled) return;
        pictureUrlCache.delete(cacheKey);
        setState({
          kind: 'unavailable',
          reason: err instanceof Error ? err.message : 'VST unavailable',
        });
      });

    return () => {
      cancelled = true;
    };
  }, [vstApiUrl, sensorName]);

  if (state.kind === 'idle') {
    return <Placeholder isDark={isDark} state="no-name" label={fallbackLabel} />;
  }
  if (state.kind === 'loading') {
    return <Placeholder isDark={isDark} state="loading" label="Loading thumbnail…" />;
  }
  if (state.kind === 'unavailable') {
    return <Placeholder isDark={isDark} state="unavailable" label={fallbackLabel} />;
  }

  if (brokenPictureUrl === state.pictureUrl) {
    return <Placeholder isDark={isDark} state="unavailable" label="Frame unavailable" />;
  }

  return (
    <img
      data-testid="vst-stream-thumbnail"
      src={state.pictureUrl}
      alt={`Recent thumbnail for ${sensorName}`}
      style={THUMBNAIL_BOX_STYLE}
      className={`object-cover rounded border ${
        isDark ? 'border-neutral-700' : 'border-gray-300'
      }`}
      onError={() => setBrokenPictureUrl(state.pictureUrl)}
    />
  );
};

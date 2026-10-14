// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Recent still frame for a registered VST sensor. Resolves `sensorName` to a
 * VST stream id via `/v1/sensor/list` (cached per `vstApiUrl`), then renders
 * `/v1/replay/stream/{id}/picture` within its latest recorded segment as an `<img>`. The
 * replay endpoint is used instead of `/v1/live/...` to avoid hitting the live
 * pipeline for what is effectively a preview.
 */

import React, { useEffect, useRef, useState } from 'react';
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
const PREVIEW_REQUEST_TIMEOUT_MS = 5_000;

const fetchPreviewJson = async (url: string): Promise<unknown> => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PREVIEW_REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) throw new Error(`VST preview request returned ${response.status}`);
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
};

interface RecordedSegment {
  startTime: string;
  endTime: string;
}

const latestRecordedSegment = (data: unknown): { start: number; end: number } | undefined => {
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
  return latest;
};

const fetchPreview = async (
  baseUrl: string,
  sensorId: string,
): Promise<{ streamId: string; startTime: string }> => {
  const streams = await fetchPreviewJson(`${baseUrl}/v1/sensor/${encodeURIComponent(sensorId)}/streams`);
  if (!Array.isArray(streams)) throw new Error('No streams available');
  const streamIds = [...new Set(streams.map((stream) => stream?.streamId)
    .filter((id): id is string => typeof id === 'string' && id.length > 0))];
  const results = await Promise.allSettled(streamIds.map(async (streamId) => {
    const timeline = await fetchPreviewJson(
      `${baseUrl}/v1/storage/${encodeURIComponent(streamId)}/timelines`,
    );
    return { streamId, segment: latestRecordedSegment(timeline) };
  }));
  let latest: { streamId: string; segment: { start: number; end: number } } | undefined;
  for (const result of results) {
    if (result.status !== 'fulfilled' || !result.value.segment) continue;
    if (!latest || result.value.segment.end > latest.segment.end) {
      latest = { streamId: result.value.streamId, segment: result.value.segment };
    }
  }
  if (!latest) throw new Error('No recorded timeline available');
  const { start, end } = latest.segment;
  const lookback = Math.min(THUMBNAIL_LOOKBACK_MS, (end - start) / 2);
  return { streamId: latest.streamId, startTime: new Date(end - lookback).toISOString() };
};

const Placeholder: React.FC<{
  isDark: boolean;
  state: 'idle' | 'loading' | 'unavailable' | 'no-name';
  label?: string;
  onRetry?: () => void;
}> = ({ isDark, state, label, onRetry }) => {
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
      {onRetry && <button type="button" className="underline" onClick={onRetry}>Retry thumbnail</button>}
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

  const [retryCount, setRetryCount] = useState(0);
  const retryRequested = useRef(false);
  const retryThumbnail = () => {
    clearVstStreamThumbnailCache(vstApiUrl, sensorName);
    setBrokenPictureUrl(null);
    setState({ kind: 'loading' });
    retryRequested.current = true;
    setRetryCount((count) => count + 1);
  };

  useEffect(() => {
    const forceRefresh = retryRequested.current;
    retryRequested.current = false;
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

    fetchSensorMap(vstApiUrl, { forceRefresh })
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
        const { streamId, startTime } = await fetchPreview(baseUrl, sensorId);
        if (cancelled) return;
        const pictureUrl = `${baseUrl}/v1/replay/stream/${encodeURIComponent(
          streamId,
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
  }, [vstApiUrl, sensorName, retryCount]);

  if (state.kind === 'idle') {
    return <Placeholder isDark={isDark} state="no-name" label={fallbackLabel} />;
  }
  if (state.kind === 'loading') {
    return <Placeholder isDark={isDark} state="loading" label="Loading thumbnail…" />;
  }
  if (state.kind === 'unavailable') {
    return <Placeholder isDark={isDark} state="unavailable" label={fallbackLabel} onRetry={retryThumbnail} />;
  }

  if (brokenPictureUrl === state.pictureUrl) {
    return <Placeholder isDark={isDark} state="unavailable" label="Frame unavailable" onRetry={retryThumbnail} />;
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

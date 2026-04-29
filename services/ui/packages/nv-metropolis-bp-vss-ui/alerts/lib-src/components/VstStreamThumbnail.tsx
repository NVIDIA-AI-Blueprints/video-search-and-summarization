// SPDX-License-Identifier: MIT
/**
 * Live still frame for a registered VST sensor. Resolves `sensorName` to a
 * VST stream id via `/v1/sensor/list` (cached per `vstApiUrl`), then renders
 * `/v1/live/stream/{id}/picture` as an `<img>`.
 */

import React, { useEffect, useState } from 'react';
import { IconCamera, IconAlertTriangle, IconLoader2 } from '@tabler/icons-react';

interface VstStreamThumbnailProps {
  vstApiUrl?: string;
  /** Friendly sensor name as registered with VST (`name` in `/v1/sensor/list`). */
  sensorName: string;
  isDark: boolean;
  fallbackLabel?: string;
}

const THUMBNAIL_BOX_STYLE: React.CSSProperties = { width: '128px', height: '72px' };

interface VstSensorListEntry {
  name?: string;
  sensorId?: string;
  state?: string;
}

// TTL ensures sensors registered elsewhere appear without a hard reload.
const SENSOR_LIST_TTL_MS = 60_000;

interface SensorMapCacheEntry {
  promise: Promise<Map<string, string>>;
  createdAt: number;
}

const sensorListCache = new Map<string, SensorMapCacheEntry>();

export const clearSensorListCache = (vstApiUrl?: string): void => {
  if (vstApiUrl) {
    sensorListCache.delete(vstApiUrl);
  } else {
    sensorListCache.clear();
  }
};

const fetchSensorMap = (
  vstApiUrl: string,
  options?: { forceRefresh?: boolean },
): Promise<Map<string, string>> => {
  const now = Date.now();
  const cached = sensorListCache.get(vstApiUrl);
  if (
    cached &&
    !options?.forceRefresh &&
    now - cached.createdAt < SENSOR_LIST_TTL_MS
  ) {
    return cached.promise;
  }

  const promise = fetch(`${vstApiUrl.replace(/\/+$/, '')}/v1/sensor/list`)
    .then((response) => {
      if (!response.ok) {
        throw new Error(`VST /v1/sensor/list returned ${response.status}`);
      }
      return response.json();
    })
    .then((data) => {
      const map = new Map<string, string>();
      if (Array.isArray(data)) {
        for (const entry of data as VstSensorListEntry[]) {
          // Online-only — same convention as useAlerts/useFilter.
          if (entry?.name && entry?.sensorId && entry.state === 'online') {
            map.set(entry.name, entry.sensorId);
          }
        }
      }
      return map;
    })
    .catch((err) => {
      // Evict failed entry so subsequent renders can retry before TTL.
      const existing = sensorListCache.get(vstApiUrl);
      if (existing && existing.promise === promise) {
        sensorListCache.delete(vstApiUrl);
      }
      throw err;
    });

  sensorListCache.set(vstApiUrl, { promise, createdAt: now });
  return promise;
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

export const VstStreamThumbnail: React.FC<VstStreamThumbnailProps> = ({
  vstApiUrl,
  sensorName,
  isDark,
  fallbackLabel,
}) => {
  const [state, setState] = useState<
    | { kind: 'idle' }
    | { kind: 'loading' }
    | { kind: 'ready'; pictureUrl: string }
    | { kind: 'unavailable'; reason: string }
  >({ kind: 'idle' });
  const [imageBroken, setImageBroken] = useState(false);

  useEffect(() => {
    setImageBroken(false);

    if (!sensorName) {
      setState({ kind: 'idle' });
      return;
    }
    if (!vstApiUrl) {
      setState({ kind: 'unavailable', reason: 'VST URL not configured' });
      return;
    }

    let cancelled = false;
    setState({ kind: 'loading' });

    fetchSensorMap(vstApiUrl)
      .then((map) => {
        if (cancelled) return;
        const sensorId = map.get(sensorName);
        if (!sensorId) {
          setState({
            kind: 'unavailable',
            reason: `Sensor "${sensorName}" not registered with VST`,
          });
          return;
        }
        const pictureUrl = `${vstApiUrl.replace(/\/+$/, '')}/v1/live/stream/${encodeURIComponent(
          sensorId,
        )}/picture`;
        setState({ kind: 'ready', pictureUrl });
      })
      .catch((err) => {
        if (cancelled) return;
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

  if (imageBroken) {
    return <Placeholder isDark={isDark} state="unavailable" label="Frame unavailable" />;
  }

  return (
    <img
      data-testid="vst-stream-thumbnail"
      src={state.pictureUrl}
      alt={`Live VST thumbnail for ${sensorName}`}
      style={THUMBNAIL_BOX_STYLE}
      className={`object-cover rounded border ${
        isDark ? 'border-neutral-700' : 'border-gray-300'
      }`}
      onError={() => setImageBroken(true)}
    />
  );
};

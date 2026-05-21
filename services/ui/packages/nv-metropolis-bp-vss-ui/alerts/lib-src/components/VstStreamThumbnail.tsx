// SPDX-License-Identifier: MIT
/**
 * Recent still frame for a registered VST sensor. Resolves `sensorName` to a
 * VST stream id via `/v1/sensor/list` (cached per `vstApiUrl`), then renders
 * `/v1/replay/stream/{id}/picture` with a short lookback as an `<img>`. The
 * replay endpoint is used instead of `/v1/live/...` to avoid hitting the live
 * pipeline for what is effectively a preview.
 */

import React, { useEffect, useState } from 'react';
import { IconCamera, IconAlertTriangle, IconLoader2 } from '@tabler/icons-react';
import { fetchSensorMap } from '../utils/vstSensorList';

export { clearSensorListCache } from '../utils/vstSensorList';

interface VstStreamThumbnailProps {
  vstApiUrl?: string;
  /** Friendly sensor name as registered with VST (`name` in `/v1/sensor/list`). */
  sensorName: string;
  isDark: boolean;
  fallbackLabel?: string;
}

const THUMBNAIL_BOX_STYLE: React.CSSProperties = { width: '128px', height: '72px' };

// Short lookback for the replay snapshot. Far enough back that the segment is
// reliably written to storage, short enough that the preview still looks fresh.
const THUMBNAIL_LOOKBACK_MS = 5_000;

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
        const startTime = new Date(Date.now() - THUMBNAIL_LOOKBACK_MS).toISOString();
        const pictureUrl = `${vstApiUrl.replace(/\/+$/, '')}/v1/replay/stream/${encodeURIComponent(
          sensorId,
        )}/picture?startTime=${encodeURIComponent(startTime)}`;
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
      alt={`Recent VST thumbnail for ${sensorName}`}
      style={THUMBNAIL_BOX_STYLE}
      className={`object-cover rounded border ${
        isDark ? 'border-neutral-700' : 'border-gray-300'
      }`}
      onError={() => setImageBroken(true)}
    />
  );
};

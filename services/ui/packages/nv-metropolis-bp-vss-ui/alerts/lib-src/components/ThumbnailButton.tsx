// SPDX-License-Identifier: MIT
/**
 * ThumbnailButton Component - Video Thumbnail with Play Overlay
 * 
 * Displays a thumbnail image from the video stream API with a play button overlay.
 * Handles loading states and errors gracefully.
 */

import React, { useMemo, useState } from 'react';
import { Button } from '@nvidia/foundations-react-core';
import { IconPlayerPlay, IconPhoto } from '@tabler/icons-react';
import { AlertData } from '../types';

interface ThumbnailButtonProps {
  alert: AlertData;
  vstApiUrl?: string;
  sensorMap?: Map<string, string>;
  isDark: boolean;
  onPlayVideo: (alert: AlertData) => void;
  isLoading?: boolean;
  showObjectsBbox?: boolean;
}

/** Fixed size + positioning context so absolute overlays stay inside the cell. */
const BUTTON_BOX_STYLE = { width: '84px', height: '64px' } as const;

export const ThumbnailButton: React.FC<ThumbnailButtonProps> = ({
  alert,
  vstApiUrl,
  sensorMap,
  isDark,
  onPlayVideo,
  isLoading = false,
  showObjectsBbox = false
}) => {
  const [imageError, setImageError] = useState(false);
  const [imageLoading, setImageLoading] = useState(true);

  const spinnerStyle = { width: '24px', height: '24px' };

  const thumbnailUrl = useMemo(() => {
    if (!vstApiUrl || !sensorMap || !alert.sensor || !alert.timestamp) {
      return null;
    }

    const sensorId = sensorMap.get(alert.sensor);
    if (!sensorId) {
      return null;
    }

    let url = `${vstApiUrl}/v1/replay/stream/${sensorId}/picture?width=256&height=114&startTime=${alert.timestamp}`;

    const objectIds = alert.metadata?.objectIds;
    if (showObjectsBbox && Array.isArray(objectIds) && objectIds.length > 0) {
      const overlay = {
        bbox: {showAll: true},
        objectId: objectIds,
        color: 'red',
        thickness: 2,
        debug: false,
        opacity: 254
      };
      url += `&overlay=${encodeURIComponent(JSON.stringify(overlay))}`;
    }

    return url;
  }, [vstApiUrl, sensorMap, alert, showObjectsBbox]);

  const handleClick = () => {
    if (isLoading) return;
    onPlayVideo(alert);
  };

  // If no thumbnail URL available, show icon
  if (!thumbnailUrl || imageError) {
    return (
      <button
        type="button"
        data-testid="alert-thumbnail"
        onClick={handleClick}
        disabled={isLoading}
        title={isLoading ? "Loading video..." : "Play video"}
        style={BUTTON_BOX_STYLE}
        className={`relative flex items-center justify-center rounded border p-2 transition-colors disabled:opacity-50 disabled:cursor-not-allowed shrink-0 ${
          isLoading ? 'cursor-wait opacity-70' : ''
        } ${
          isDark
            ? 'text-gray-300 border-gray-600 hover:border-gray-500 hover:bg-gray-700'
            : 'text-gray-600 border-gray-300 hover:border-gray-400 hover:bg-gray-100'
        }`}
      >
        {isLoading ? (
          <div className="animate-spin rounded-full h-5 w-5 border-2 border-current border-t-transparent" />
        ) : (
          <IconPlayerPlay className="w-5 h-5 fill-current" />
        )}
      </button>
    );
  }

  return (
    <Button
      data-testid="alert-thumbnail"
      kind="tertiary"
      onClick={handleClick}
      disabled={isLoading}
      title={isLoading ? "Loading video..." : "Play video"}
      style={BUTTON_BOX_STYLE}
      className={`relative overflow-hidden group shrink-0 rounded border p-0 min-h-0 min-w-0 ${
        isDark ? 'border-gray-600 hover:border-gray-500' : 'border-gray-300 hover:border-gray-400'
      } ${isLoading ? 'cursor-wait' : ''}`}
    >
      {/* Loading State - Show spinner while loading thumbnail */}
      {imageLoading && !isLoading && (
        <div className={`pointer-events-none absolute inset-0 flex items-center justify-center ${
          isDark ? 'bg-neutral-900' : 'bg-gray-100'
        }`}>
          <div className="relative">
            <IconPhoto className={`w-6 h-6 ${
              isDark ? 'text-gray-600' : 'text-gray-300'
            }`} />
            <div className={`absolute inset-0 border-2 border-transparent rounded-full animate-spin ${
              isDark ? 'border-t-gray-400' : 'border-t-gray-500'
            }`} style={spinnerStyle} />
          </div>
        </div>
      )}

      {/* Thumbnail Image */}
      <img
        src={thumbnailUrl}
        alt="Video thumbnail"
        className={`h-full w-full object-cover transition-opacity duration-300 ${
          imageLoading ? 'opacity-0' : 'opacity-100'
        }`}
        onLoad={() => setImageLoading(false)}
        onError={() => {
          setImageError(true);
          setImageLoading(false);
        }}
      />

      {/* Video Loading Overlay - Show when checking video URL */}
      {isLoading && (
        <div className={`pointer-events-none absolute inset-0 flex items-center justify-center ${
          isDark ? 'bg-black/60' : 'bg-black/40'
        }`}>
          <div className="animate-spin rounded-full h-6 w-6 border-2 border-white border-t-transparent" />
        </div>
      )}

      {/* Play Overlay - Only show when not loading */}
      {!imageLoading && !isLoading && (
        <div className={`pointer-events-none absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity ${
          isDark ? 'bg-black/50' : 'bg-black/30'
        }`}>
          <div className="bg-white/90 rounded-full p-2">
            <IconPlayerPlay className="w-5 h-5 fill-current text-gray-800" />
          </div>
        </div>
      )}
    </Button>
  );
};


// SPDX-License-Identifier: MIT
/**
 * ThumbnailButton Component - Video Thumbnail with Play Overlay
 * 
 * Displays a thumbnail image from the video stream API with a play button overlay.
 * Handles loading states and errors gracefully.
 */

import React, { useState } from 'react';
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

export const ThumbnailButton: React.FC<ThumbnailButtonProps> = ({
  alert,
  vstApiUrl,
  sensorMap,
  isDark,
  onPlayVideo,
  isLoading,
  showObjectsBbox = false
}) => {
  const [imageError, setImageError] = useState(false);
  const [imageLoading, setImageLoading] = useState(true);

  // Button dimensions
  const buttonStyle = { width: '84px', height: '64px' };
  const spinnerStyle = { width: '24px', height: '24px' };

  // Get thumbnail URL
  const getThumbnailUrl = () => {
    if (!vstApiUrl || !sensorMap || !alert.sensor || !alert.timestamp) {
      return null;
    }

    const sensorId = sensorMap.get(alert.sensor);
    if (!sensorId) {
      return null;
    }

    let url = `${vstApiUrl}/v1/replay/stream/${sensorId}/picture?width=256&height=114&startTime=${alert.timestamp}`;

    // Add overlay with bounding boxes if objectIds are available and feature is enabled
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
  };

  const thumbnailUrl = getThumbnailUrl();

  const buttonClass = `relative group cursor-pointer rounded border overflow-hidden transition-all ${
    isDark 
      ? 'border-gray-600 hover:border-gray-500 bg-gray-700' 
      : 'border-gray-300 hover:border-gray-400 bg-gray-100'
  }`;

  const handleClick = () => {
    if (isLoading) return;
    onPlayVideo(alert);
  };

  // If no thumbnail URL available, show icon
  if (!thumbnailUrl || imageError) {
    return (
      <button
        onClick={handleClick}
        disabled={isLoading}
        className={`relative rounded border flex items-center justify-center transition-colors ${
          isLoading ? 'cursor-wait opacity-70' : ''
        } ${
          isDark 
            ? 'text-gray-300 border-gray-600 hover:border-gray-500 hover:bg-gray-700' 
            : 'text-gray-600 border-gray-300 hover:border-gray-400 hover:bg-gray-100'
        }`}
        title={isLoading ? "Loading video..." : "Play video"}
        style={buttonStyle}
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
    <button
      onClick={handleClick}
      disabled={isLoading}
      className={`${buttonClass} ${isLoading ? 'cursor-wait' : ''}`}
      title={isLoading ? "Loading video..." : "Play video"}
      style={buttonStyle}
    >
      {/* Loading State - Show spinner while loading thumbnail */}
      {imageLoading && !isLoading && (
        <div className={`absolute inset-0 flex items-center justify-center ${
          isDark ? 'bg-gray-700' : 'bg-gray-100'
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
        className={`w-full h-full object-cover transition-opacity duration-300 ${
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
        <div className={`absolute inset-0 flex items-center justify-center ${
          isDark ? 'bg-black/60' : 'bg-black/40'
        }`}>
          <div className="animate-spin rounded-full h-6 w-6 border-2 border-white border-t-transparent" />
        </div>
      )}

      {/* Play Overlay - Only show when not loading */}
      {!imageLoading && !isLoading && (
        <div className={`absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity ${
          isDark ? 'bg-black/50' : 'bg-black/30'
        }`}>
          <div className="bg-white/90 rounded-full p-2">
            <IconPlayerPlay className="w-5 h-5 fill-current text-gray-800" />
          </div>
        </div>
      )}
    </button>
  );
};


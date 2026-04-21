// SPDX-License-Identifier: MIT
import React, { useState, useEffect, useRef, useCallback } from 'react';
import type { StreamInfo } from '../types';
import { getFileExtension, isRtspStream, fetchPictureWithQueue } from '../utils';
import { createApiEndpoints } from '../api';
import { copyToClipboard } from '@nemo-agent-toolkit/ui';
import { IconCheck, IconCopy } from '@tabler/icons-react';

interface StreamCardProps {
  stream: StreamInfo;
  isSelected: boolean;
  vstApiUrl?: string | null;
  onSelectionChange: (streamId: string, selected: boolean) => void;
  getEndTimeForStream: (streamId: string) => string | null;
}

export const StreamCard: React.FC<StreamCardProps> = ({
  stream,
  isSelected,
  vstApiUrl,
  onSelectionChange,
  getEndTimeForStream,
}) => {
  const extension = getFileExtension(stream.url);
  const isRtsp = isRtspStream(stream);
  const [thumbnailUrl, setThumbnailUrl] = useState<string | null>(null);
  const [isLoadingThumbnail, setIsLoadingThumbnail] = useState(true);
  const [thumbnailError, setThumbnailError] = useState(false);
  const currentObjectUrlRef = useRef<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    const fetchThumbnail = async () => {
      if (!vstApiUrl) {
        setThumbnailError(true);
        setIsLoadingThumbnail(false);
        return;
      }

      const apiEndpoints = createApiEndpoints(vstApiUrl);
      setIsLoadingThumbnail(true);
      setThumbnailError(false);

      try {
        let pictureUrl: string;

        if (isRtsp) {
          pictureUrl = apiEndpoints.LIVE_PICTURE(stream.streamId);
        } else {
          const endTime = getEndTimeForStream(stream.streamId);
          if (!endTime) throw new Error('No timeline available');
          pictureUrl = apiEndpoints.REPLAY_PICTURE(stream.streamId, endTime);
        }

        const blob = await fetchPictureWithQueue(pictureUrl);
        const newUrl = URL.createObjectURL(blob);

        if (isMounted) {
          if (currentObjectUrlRef.current) {
            URL.revokeObjectURL(currentObjectUrlRef.current);
          }
          currentObjectUrlRef.current = newUrl;
          setThumbnailUrl(newUrl);
        } else {
          URL.revokeObjectURL(newUrl);
        }
      } catch {
        if (isMounted) setThumbnailError(true);
      } finally {
        if (isMounted) setIsLoadingThumbnail(false);
      }
    };

    fetchThumbnail();
    return () => { isMounted = false; };
  }, [stream.streamId, isRtsp, vstApiUrl, getEndTimeForStream]);

  useEffect(() => {
    return () => {
      if (currentObjectUrlRef.current) {
        URL.revokeObjectURL(currentObjectUrlRef.current);
        currentObjectUrlRef.current = null;
      }
    };
  }, []);

  const [copyState, setCopyState] = useState<'idle' | 'success' | 'error'>('idle');
  const copyTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    return () => {
      if (copyTimeoutRef.current) {
        clearTimeout(copyTimeoutRef.current);
      }
    };
  }, []);

  const handleCheckboxChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    onSelectionChange(stream.streamId, e.target.checked);
  };

  const handleCopyContext = useCallback(async () => {
    const text = JSON.stringify(
      { sensorName: stream.name, streamId: stream.streamId },
      null,
      2
    );
    try {
      await copyToClipboard(text);
      setCopyState('success');
    } catch {
      setCopyState('error');
    }
    if (copyTimeoutRef.current) clearTimeout(copyTimeoutRef.current);
    copyTimeoutRef.current = setTimeout(() => {
      setCopyState('idle');
      copyTimeoutRef.current = null;
    }, 2000);
  }, [stream.name, stream.streamId]);

  return (
    <div
      className={`rounded-lg border overflow-hidden bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 ${isSelected ? 'ring-2 ring-green-500' : ''}`}
    >
      <div className="flex items-center gap-2 px-3 py-2 bg-gray-50 dark:bg-gray-800">
        <input
          type="checkbox"
          checked={isSelected}
          onChange={handleCheckboxChange}
          className="w-4 h-4 rounded border-2 cursor-pointer bg-white dark:bg-gray-700 border-gray-300 dark:border-gray-600 text-green-600 dark:text-green-500 focus:ring-green-500"
        />
        <p
          className="text-sm font-medium truncate flex-1 text-gray-800 dark:text-gray-200 min-w-0"
          title={stream.name}
        >
          {stream.name}
        </p>
        <button
          type="button"
          onClick={handleCopyContext}
          className="flex-shrink-0 px-2 py-1 rounded transition-colors text-[11px] font-medium flex items-center gap-1 bg-blue-500 hover:bg-blue-600 dark:bg-blue-600 dark:hover:bg-blue-700 text-white"
          title="Copy sensor context"
        >
          {copyState === 'success' ? (
            <IconCheck className="w-2.5 h-2.5" />
          ) : (
            <IconCopy className="w-2.5 h-2.5" />
          )}
          <span>
            {copyState === 'success' ? 'Copied' : copyState === 'error' ? 'Failed' : 'Copy'}
          </span>
        </button>
      </div>

      <div
        className="relative flex items-center justify-center bg-gray-100 dark:bg-gray-900 pb-[56.25%]"
      >
        {thumbnailUrl && !thumbnailError ? (
          <img src={thumbnailUrl} alt={stream.name} className="absolute inset-0 w-full h-full object-cover" />
        ) : isLoadingThumbnail ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="animate-pulse w-8 h-8 rounded-full bg-gray-600" />
          </div>
        ) : (
          <div className="absolute inset-0 flex items-center justify-center bg-gray-200 dark:bg-gray-800">
            <div className="flex items-center justify-center w-12 h-12 rounded-full bg-gray-300 dark:bg-gray-700">
              <svg
                className="text-gray-500 dark:text-gray-400"
                width="24"
                height="24"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
            </div>
          </div>
        )}

        <div className="absolute top-2 left-2 px-2 py-0.5 rounded text-xs font-medium bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300">
          {isRtsp ? 'RTSP' : extension || 'VIDEO'}
        </div>
      </div>

      <div className="px-3 py-2">
        <div className="flex items-center justify-end gap-2">
          {stream.metadata.codec && (
            <span className="text-xs text-gray-500">
              {stream.metadata.codec.toUpperCase()}
            </span>
          )}
          {stream.metadata.framerate && (
            <span className="text-xs text-gray-500">
              {parseFloat(stream.metadata.framerate).toFixed(0)} fps
            </span>
          )}
        </div>
      </div>
    </div>
  );
};

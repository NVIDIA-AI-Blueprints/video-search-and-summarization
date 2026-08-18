// SPDX-License-Identifier: MIT
import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Button } from '@nvidia/foundations-react-core';
import type { StreamInfo, ChatSidebarQueryContext } from '../types';
import { getFileExtension, isRtspStream, fetchPictureWithQueue, getStreamType } from '../utils';
import { createApiEndpoints } from '../api';
import { copyToClipboard } from 'common';
import { IconCheck } from '@tabler/icons-react';

interface StreamCardProps {
  stream: StreamInfo;
  isSelected: boolean;
  vstApiUrl?: string | null;
  onSelectionChange: (streamId: string, selected: boolean) => void;
  getEndTimeForStream: (streamId: string) => string | null;
  /** Resolving to `false` means playback could not be opened, and the card shows a notice. */
  onPlay?: (stream: StreamInfo) => void | Promise<boolean | void>;
  isLoadingPlay?: boolean;
  /** When set, adds this stream as a chip in the app Chat sidebar (and still copies JSON). */
  onAddChatQueryContext?: (ctx: ChatSidebarQueryContext) => void;
}

export const StreamCard: React.FC<StreamCardProps> = ({
  stream,
  isSelected,
  vstApiUrl,
  onSelectionChange,
  getEndTimeForStream,
  onPlay,
  isLoadingPlay = false,
  onAddChatQueryContext,
}) => {
  const extension = getFileExtension(stream.url);
  const isRtsp = isRtspStream(stream);
  const [thumbnailUrl, setThumbnailUrl] = useState<string | null>(null);
  const [isLoadingThumbnail, setIsLoadingThumbnail] = useState(true);
  const [thumbnailError, setThumbnailError] = useState(false);
  const [playbackFailed, setPlaybackFailed] = useState(false);
  const currentObjectUrlRef = useRef<string | null>(null);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    let isMounted = true;
    let retryTimer: NodeJS.Timeout | undefined;

    const fetchThumbnail = async (retryCount = 0) => {
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
          if (!endTime) {
            if (retryCount < 5 && isMounted) {
              retryTimer = setTimeout(() => fetchThumbnail(retryCount + 1), 1000);
              return;
            }
            throw new Error('No timeline available');
          }
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
    return () => {
      isMounted = false;
      if (retryTimer) clearTimeout(retryTimer);
    };
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

  // VST has no recorded footage for a stream that was just added, so the URL
  // request 404s and the modal never opens. Surface that instead of no-op'ing.
  const handlePlayClick = useCallback(async () => {
    if (!onPlay || isLoadingPlay) return;
    setPlaybackFailed(false);
    const opened = await onPlay(stream);
    if (isMountedRef.current && opened === false) {
      setPlaybackFailed(true);
    }
  }, [onPlay, isLoadingPlay, stream]);

  const handleCopyContext = useCallback(async () => {
    const data = {
      sensorName: stream.name,
      streamId: stream.streamId,
      mediaType: getStreamType(stream),
    };
    const text = JSON.stringify(data, null, 2);

    onAddChatQueryContext?.({
      id: `video-mgmt-stream:${stream.streamId}`,
      label: stream.name,
      // contextType: UI-only (chip tooltip / future grouping); not sent to the backend — see Chat onSend.
      contextType: 'media/video',
      data,
    });

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
  }, [stream, onAddChatQueryContext]);

  return (
    <div
      className={`rounded-lg border overflow-hidden bg-white dark:bg-neutral-900 border-gray-200 dark:border-gray-700 ${isSelected ? 'ring-2 ring-green-500' : ''}`}
    >
      <div className="flex items-center gap-2 px-3 py-2 bg-gray-50 dark:bg-neutral-900">
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
        {onAddChatQueryContext ? (
          <Button
            kind="primary"
            size="small"
            className="flex-shrink-0 text-xs"
            onClick={handleCopyContext}
            title="Add sensor context to chat"
          >
            {copyState === 'success' ? (
              <>
                <IconCheck className="w-2.5 h-2.5 shrink-0" style={{ color: 'inherit' }} />
                <span>Added</span>
              </>
            ) : copyState === 'error' ? (
              <span>Failed</span>
            ) : (
              <span>+ Chat</span>
            )}
          </Button>
        ) : null}
      </div>

      <div
        className="group relative flex items-center justify-center bg-gray-100 dark:bg-neutral-900 pb-[56.25%]"
      >
        {thumbnailUrl && !thumbnailError ? (
          <img src={thumbnailUrl} alt={stream.name} className="absolute inset-0 w-full h-full object-cover" />
        ) : isLoadingThumbnail ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="animate-pulse w-8 h-8 rounded-full bg-gray-600" />
          </div>
        ) : (
          <div className="absolute inset-0 flex items-center justify-center bg-gray-200 dark:bg-neutral-900">
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

        {onPlay && (
          <button
            type="button"
            onClick={handlePlayClick}
            disabled={isLoadingPlay}
            className={`absolute inset-0 flex items-center justify-center transition-colors duration-200 ${
              isLoadingPlay
                ? 'bg-black/40 cursor-wait'
                : 'bg-black/0 group-hover:bg-black/40 cursor-pointer'
            }`}
            aria-label={isLoadingPlay ? `Loading ${stream.name}` : `Play ${stream.name}`}
          >
            <div className={`w-12 h-12 flex items-center justify-center rounded-full bg-white/90 dark:bg-gray-800/90 shadow-lg transition-opacity duration-200 ${
              isLoadingPlay ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
            }`}>
              {isLoadingPlay ? (
                <svg className="w-6 h-6 text-gray-800 dark:text-white animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
              ) : (
                <svg className="w-6 h-6 text-gray-800 dark:text-white ml-0.5" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M8 5v14l11-7z" />
                </svg>
              )}
            </div>
          </button>
        )}

        {playbackFailed && (
          <div
            role="status"
            data-testid="playback-notice"
            className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-2 p-3 text-center bg-gray-200/95 dark:bg-neutral-900/95"
          >
            <div className="flex items-center gap-1.5 text-red-600 dark:text-red-500">
              <svg
                aria-hidden="true"
                data-testid="playback-warning-icon"
                className="h-4 w-4 shrink-0 text-red-600 dark:text-red-500"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M10.3 2.9 1.8 17a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 2.9a2 2 0 0 0-3.4 0Z" />
                <path d="M12 9v4" />
                <path d="M12 17h.01" />
              </svg>
              <p className="text-xs font-medium leading-snug">
                {isRtsp ? 'RTSP preview not ready. Retry shortly.' : 'Preview not ready. Retry shortly.'}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handlePlayClick}
                disabled={isLoadingPlay}
                data-testid="playback-notice-retry"
                className="rounded px-2 py-1 text-xs font-medium transition-colors bg-gray-300 text-gray-800 hover:bg-gray-400 dark:bg-gray-700 dark:text-gray-100 dark:hover:bg-gray-600 disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-gray-500"
              >
                Retry
              </button>
              <button
                type="button"
                onClick={() => setPlaybackFailed(false)}
                data-testid="playback-notice-dismiss"
                className="rounded px-2 py-1 text-xs transition-colors text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-gray-500"
              >
                Dismiss
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="px-3 py-2">
        <div className="flex items-center justify-end gap-2">
          {stream.metadata.codec && (
            <span className="text-xs text-gray-500 dark:text-gray-400">
              {stream.metadata.codec.toUpperCase()}
            </span>
          )}
          {stream.metadata.framerate && (
            <span className="text-xs text-gray-500 dark:text-gray-400">
              {parseFloat(stream.metadata.framerate).toFixed(0)} fps
            </span>
          )}
        </div>
      </div>
    </div>
  );
};

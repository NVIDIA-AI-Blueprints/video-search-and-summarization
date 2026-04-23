// SPDX-License-Identifier: MIT
import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { Button } from '@nvidia/foundations-react-core';
import { IconInbox, IconMessagePlus, IconCheck } from '@tabler/icons-react';
import { Whisper, Tooltip } from 'rsuite';
import { SearchData, QueryDataContext } from '../types';
import { formatTime, parseDateAsLocal } from '../utils/Formatter';

const AddContextButton: React.FC<{ item: SearchData; onAddContext?: (ctx: QueryDataContext) => void }> = ({ item, onAddContext }) => {
  const [addedState, setAddedState] = useState<'idle' | 'success'>('idle');
  const timeoutRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  const handleClick = useCallback(() => {
    if (!onAddContext) return;
    const ctx: QueryDataContext = {
      id: `${item.video_name}-${item.start_time}-${item.end_time}`,
      label: item.video_name,
      type: 'sensor-clip',
      data: { sensorName: item.video_name, startTime: item.start_time, endTime: item.end_time },
    };
    onAddContext(ctx);
    setAddedState('success');
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => {
      setAddedState('idle');
      timeoutRef.current = null;
    }, 2000);
  }, [item.video_name, item.start_time, item.end_time, onAddContext]);

  return (
    <Button
      kind="tertiary"
      size="small"
      onClick={handleClick}
      disabled={!onAddContext}
      title="Add to Chat"
    >
      {addedState === 'success' ? (
        <IconCheck className="w-2.5 h-2.5" style={{ color: 'inherit' }} />
      ) : (
        <IconMessagePlus className="w-2.5 h-2.5" style={{ color: 'inherit' }} />
      )}
      <span className="text-xs">
        {addedState === 'success' ? 'Added' : 'Add to Chat'}
      </span>
    </Button>
  );
};

interface VideoSearchListProps {
  data: SearchData[];
  loading: boolean;
  error: string | null;
  isDark: boolean;
  onRefresh: () => void;
  onPlayVideo: (data: SearchData, showObjectsBbox: boolean) => void;
  showObjectsBbox?: boolean;
  onAddContext?: (ctx: QueryDataContext) => void;
}

const EmptyState: React.FC<{ isDark: boolean }> = ({ isDark }) => (
  <div className="p-4">
    <div className="flex flex-col items-center justify-center py-8 text-center">
      <IconInbox className={`w-12 h-12 mb-3 ${isDark ? 'text-gray-500' : 'text-gray-400'}`} stroke={1.5} />
      <p className={isDark ? 'text-gray-400' : 'text-gray-600'}>Results will update here</p>
    </div>
  </div>
);

interface ErrorStateProps {
  error: string;
  isDark: boolean;
  onRefresh: () => void;
}

const ErrorState: React.FC<ErrorStateProps> = ({ error, isDark, onRefresh }) => (
  <div className="flex items-center justify-center h-full p-4">
    <div className={`w-full max-w-2xl p-6 rounded-lg ${isDark ? 'bg-red-500/10 border border-red-500/20' : 'bg-red-50 border border-red-200'}`}>
      <div className="flex items-center gap-2 mb-3">
        <svg className={`w-5 h-5 flex-shrink-0 ${isDark ? 'text-red-400' : 'text-red-600'}`} fill="currentColor" viewBox="0 0 20 20">
          <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
        </svg>
        <p className={`font-bold text-lg ${isDark ? 'text-red-400' : 'text-red-700'}`}>Error loading items</p>
      </div>
      <div 
        className={`text-sm mb-4 p-3 rounded max-h-48 overflow-y-auto ${isDark ? 'bg-gray-800/50 text-gray-300' : 'bg-white text-red-600 border border-red-100'}`}
        style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
      >
        {error}
      </div>
      <div className="flex justify-end">
        <button 
          onClick={onRefresh}
          className="px-5 py-2.5 rounded-md font-medium transition-colors bg-blue-600 hover:bg-blue-700 text-white"
        >
          Retry
        </button>
      </div>
    </div>
  </div>
);

const CRITIC_SORT_ORDER: Record<string, number> = {
  confirmed: 0,
  unverified: 1,
  rejected: 2,
};

function getCriticSortRank(item: SearchData): number {
  return item.critic_result
    ? (CRITIC_SORT_ORDER[item.critic_result.result] ?? 3)
    : 3;
}

interface VideoCardProps {
  item: SearchData;
  index: number;
  isDark: boolean;
  showObjectsBbox: boolean;
  onPlayVideo: (data: SearchData, showObjectsBbox: boolean) => void;
  onAddContext?: (ctx: QueryDataContext) => void;
}

const VideoCard: React.FC<VideoCardProps> = ({
  item,
  index,
  isDark,
  showObjectsBbox,
  onPlayVideo,
  onAddContext,
}) => {
  const [isOpeningVideo, setIsOpeningVideo] = useState(false);
  const openingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (openingTimeoutRef.current) clearTimeout(openingTimeoutRef.current);
    };
  }, []);

  const handleOpenVideo = useCallback(() => {
    setIsOpeningVideo(true);
    onPlayVideo(item, showObjectsBbox);
    if (openingTimeoutRef.current) clearTimeout(openingTimeoutRef.current);
    openingTimeoutRef.current = setTimeout(() => {
      setIsOpeningVideo(false);
      openingTimeoutRef.current = null;
    }, 900);
  }, [item, onPlayVideo, showObjectsBbox]);

  return (
    <div 
      data-testid="search-result-card"
      key={`${item.video_name}-${index}`}
      className={`rounded-lg overflow-hidden bg-white shadow-sm dark:bg-neutral-900 w-[280px] min-w-[280px] max-w-[280px] box-border border ${
        item.critic_result?.result === 'confirmed'
          ? 'border-green-500 dark:border-green-400'
          : item.critic_result?.result === 'rejected'
          ? 'border-red-500 dark:border-red-400'
          : item.critic_result?.result === 'unverified'
          ? 'border-yellow-500 dark:border-yellow-400'
          : 'border-gray-200 dark:border-gray-600'
      }`}
    >
      <div className="p-4 pb-0 space-y-3">
        <div className="flex items-center gap-2">
          <Whisper
            placement="top"
            trigger="hover"
            speaker={<Tooltip>{item.video_name}</Tooltip>}
          >
            <h3 className="font-medium text-sm truncate cursor-default flex-1 min-w-0">
              {item.video_name}
            </h3>
          </Whisper>
          <AddContextButton item={item} onAddContext={onAddContext} />
        </div>
        <div className="rounded-lg relative aspect-video group cursor-pointer">
          <div className="rounded-lg absolute inset-0 bg-gradient-to-br from-gray-700 to-gray-900">
            <img src={item.screenshot_url} alt={item.video_name} className="rounded-lg w-full h-full object-cover" />
          </div>
          
          <button
            type="button"
            data-testid="video-play-overlay"
            className={`absolute inset-0 flex items-center justify-center bg-transparent border-none p-0 ${isOpeningVideo ? 'cursor-wait' : 'cursor-pointer'}`}
            onClick={handleOpenVideo}
            disabled={isOpeningVideo}
            aria-label={`Play ${item.video_name}`}
          >
            <div className="w-12 h-12 sm:w-14 sm:h-14 rounded-lg bg-[#76b900]/50 flex items-center justify-center shadow-lg transition-transform hover:scale-110 border border-white/30">
              {isOpeningVideo ? (
                <div className="h-6 w-6 sm:h-7 sm:w-7 animate-spin rounded-full border-2 border-white border-t-transparent" />
              ) : (
                <svg className="w-6 h-6 sm:w-7 sm:h-7 text-white ml-0.5" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M8 5v14l11-7z" />
                </svg>
              )}
            </div>
          </button>
          
          <div className="rounded-b-lg absolute bottom-0 left-0 right-0 px-4 py-2 bg-gradient-to-t from-black/70 to-transparent flex items-end justify-between">
            <div className="text-white text-xs">
              <span className="font-medium">{formatTime(parseDateAsLocal(item.start_time))}</span>
              <span className="mx-1">/</span>
              <span className="font-medium">{formatTime(parseDateAsLocal(item.end_time))}</span>
            </div>
            {item.description && (
              <Whisper
                placement="top"
                trigger="hover"
                speaker={<Tooltip>{item.description}</Tooltip>}
              >
                <div className="flex items-center gap-1 bg-white/20 backdrop-blur-sm rounded-full px-2 py-1 cursor-default">
                  <svg className="w-4 h-4 text-white" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
                  </svg>
                </div>
              </Whisper>
            )}
          </div>
        </div>
      </div>

    <div className="p-4 pt-0 space-y-2 my-2">
      <div className="flex justify-between items-baseline">
        <div className="flex items-center justify-between">
        </div>
        <div className="flex items-center justify-between text-xs">
          <span className={isDark ? 'text-gray-400' : 'text-gray-600'}>
            Similarity:
          </span>
          <span data-testid="search-result-similarity" className="bg-gray-200 dark:bg-neutral-800 dark:text-white text-gray-900 font-semibold ml-1 px-3 py-1 rounded-md">
            {item.similarity.toFixed(2)}
          </span>
        </div>
      </div>
      {item.critic_result && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <span className={`inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full ${
              item.critic_result.result === 'confirmed'
                ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400'
                : item.critic_result.result === 'rejected'
                ? 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400'
                : 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400'
            }`}>
              {item.critic_result.result === 'confirmed' ? '✓' : item.critic_result.result === 'rejected' ? '✗' : '?'}
              {' '}{item.critic_result.result.charAt(0).toUpperCase() + item.critic_result.result.slice(1)}
            </span>
          </div>
          {Object.keys(item.critic_result.criteria_met).length > 0 && (
            <div className="flex flex-wrap gap-1">
              {Object.entries(item.critic_result.criteria_met).map(([criterion, met]) => (
                <span
                  key={criterion}
                  className={`text-[10px] px-1.5 py-0.5 rounded ${
                    met
                      ? 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400'
                      : 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-400'
                  }`}
                >
                  {met ? '✓' : '✗'} {criterion}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
    </div>
  );
};


export const VideoSearchList: React.FC<VideoSearchListProps> = ({
    data,
    loading,
    error,
    isDark,
    onRefresh,
    onPlayVideo,
    showObjectsBbox = false,
    onAddContext,
}) => {
    const sortedData = useMemo(() => {
      const hasCritic = data.some((item) => item.critic_result);
      if (!hasCritic) return data;

      return [...data].sort((a, b) => {
        const rankDiff = getCriticSortRank(a) - getCriticSortRank(b);
        if (rankDiff !== 0) return rankDiff;
        return (Number(b.similarity) || 0) - (Number(a.similarity) || 0);
      });
    }, [data]);

    if (loading) {
      return <EmptyState isDark={isDark} />;
    }

    if (error) {
      return <ErrorState error={error} isDark={isDark} onRefresh={onRefresh} />;
    }

    if (data.length === 0) {
      return <EmptyState isDark={isDark} />;
    }

    return (
      <div className="p-4">
        <div data-testid="search-results-grid" className="grid gap-4 grid-cols-[repeat(auto-fill,280px)] justify-start">
          {sortedData.map((item, index) => (
            <VideoCard
              key={`${item.video_name}-${index}`}
              item={item}
              index={index}
              isDark={isDark}
              showObjectsBbox={showObjectsBbox}
              onPlayVideo={onPlayVideo}
              onAddContext={onAddContext}
            />
          ))}
        </div>
      </div>
    );
}

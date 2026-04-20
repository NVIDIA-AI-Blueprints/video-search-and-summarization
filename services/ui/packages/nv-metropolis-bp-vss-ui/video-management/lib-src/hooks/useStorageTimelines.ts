// SPDX-License-Identifier: MIT
import { useState, useEffect, useCallback, useRef } from 'react';
import type { StorageSizeResponse, StreamStorageInfo } from '../types';
import { createApiEndpoints } from '../api';

interface UseStorageTimelinesOptions {
  vstApiUrl?: string | null;
}

interface TimelineRange {
  startTime: string;
  endTime: string;
}

interface UseStorageTimelinesResult {
  timelines: Map<string, StreamStorageInfo>;
  isLoading: boolean;
  error: string | null;
  refetch: () => void;
  getEndTimeForStream: (streamId: string) => string | null;
  getTimelineRangeForStream: (streamId: string) => TimelineRange | null;
}

export function useStorageTimelines({ vstApiUrl }: UseStorageTimelinesOptions = {}): UseStorageTimelinesResult {
  const [timelines, setTimelines] = useState<Map<string, StreamStorageInfo>>(new Map());
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const timelinesRef = useRef(timelines);
  timelinesRef.current = timelines;

  const fetchTimelines = useCallback(async () => {
    if (!vstApiUrl) {
      setError('VST API URL not configured');
      setIsLoading(false);
      return;
    }

    const apiEndpoints = createApiEndpoints(vstApiUrl);
    setIsLoading(true);
    setError(null);

    try {
      const response = await fetch(apiEndpoints.STORAGE_SIZE);
      if (!response.ok) {
        throw new Error(`Failed to fetch storage timelines: ${response.status}`);
      }

      const data: StorageSizeResponse = await response.json();
      const timelinesMap = new Map<string, StreamStorageInfo>();

      for (const [key, value] of Object.entries(data)) {
        if (key !== 'total' && 'timelines' in value) {
          timelinesMap.set(key, value as StreamStorageInfo);
        }
      }

      setTimelines(timelinesMap);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch storage timelines');
    } finally {
      setIsLoading(false);
    }
  }, [vstApiUrl]);

  useEffect(() => {
    fetchTimelines();
  }, [fetchTimelines]);

  // Returns a timestamp 5 seconds before the end of the last timeline segment
  const getEndTimeForStream = useCallback((streamId: string): string | null => {
    const storageInfo = timelinesRef.current.get(streamId);
    if (!storageInfo?.timelines?.length) return null;

    const lastTimeline = storageInfo.timelines[storageInfo.timelines.length - 1];
    const startTime = new Date(lastTimeline.startTime);
    const endTime = new Date(lastTimeline.endTime);
    const durationSeconds = (endTime.getTime() - startTime.getTime()) / 1000;

    if (durationSeconds < 5) return lastTimeline.endTime;
    return new Date(endTime.getTime() - 5000).toISOString();
  }, []);

  // Returns the full time range across all timeline segments for a stream
  const getTimelineRangeForStream = useCallback((streamId: string): TimelineRange | null => {
    const storageInfo = timelinesRef.current.get(streamId);
    if (!storageInfo?.timelines?.length) return null;

    let earliestStart = new Date(storageInfo.timelines[0].startTime);
    let latestEnd = new Date(storageInfo.timelines[0].endTime);

    for (const timeline of storageInfo.timelines) {
      const start = new Date(timeline.startTime);
      const end = new Date(timeline.endTime);
      if (start < earliestStart) earliestStart = start;
      if (end > latestEnd) latestEnd = end;
    }

    return {
      startTime: earliestStart.toISOString(),
      endTime: latestEnd.toISOString(),
    };
  }, []);

  return {
    timelines,
    isLoading,
    error,
    refetch: fetchTimelines,
    getEndTimeForStream,
    getTimelineRangeForStream,
  };
}


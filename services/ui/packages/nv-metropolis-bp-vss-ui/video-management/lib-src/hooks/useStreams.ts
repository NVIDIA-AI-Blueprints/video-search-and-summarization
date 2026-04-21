// SPDX-License-Identifier: MIT
import { useState, useEffect, useCallback } from 'react';
import type { StreamInfo, StreamsApiResponse } from '../types';
import { createApiEndpoints } from '../api';
import { parseStreamsResponse } from '../utils';

interface UseStreamsOptions {
  vstApiUrl?: string | null;
}

interface UseStreamsResult {
  streams: StreamInfo[];
  isLoading: boolean;
  error: string | null;
  refetch: () => void;
}

export function useStreams({ vstApiUrl }: UseStreamsOptions = {}): UseStreamsResult {
  const [streams, setStreams] = useState<StreamInfo[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStreams = useCallback(async () => {
    if (!vstApiUrl) {
      setError('VST API URL not configured');
      setIsLoading(false);
      return;
    }

    const apiEndpoints = createApiEndpoints(vstApiUrl);
    setIsLoading(true);
    setError(null);

    try {
      const response = await fetch(apiEndpoints.STREAMS);

      if (!response.ok) {
        throw new Error(`Failed to fetch streams: ${response.status}`);
      }

      const data: StreamsApiResponse = await response.json();
      const allStreams = parseStreamsResponse(data);
      setStreams(allStreams);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error('Error fetching streams:', err);
      setError(err instanceof Error ? err.message : 'Failed to fetch streams');
    } finally {
      setIsLoading(false);
    }
  }, [vstApiUrl]);

  useEffect(() => {
    fetchStreams();
  }, [fetchStreams]);

  return {
    streams,
    isLoading,
    error,
    refetch: fetchStreams,
  };
}


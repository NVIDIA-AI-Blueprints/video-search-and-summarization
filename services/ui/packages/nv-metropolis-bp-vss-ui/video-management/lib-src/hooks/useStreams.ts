// SPDX-License-Identifier: MIT
import { useState, useEffect, useCallback, useRef } from 'react';
import type { StreamInfo, StreamsApiResponse } from '../types';
import { createApiEndpoints } from '../api';
import { parseStreamsResponse } from '../utils';
import { DELETED_STREAM_POLL_DELAYS_MS } from '../constants';

interface UseStreamsOptions {
  vstApiUrl?: string | null;
}

interface WaitUntilStreamsRemovedResult {
  /** sensorIds that were still present in VST after the poll budget ran out */
  remainingSensorIds: string[];
}

interface UseStreamsResult {
  streams: StreamInfo[];
  isLoading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
  /**
   * Re-poll VST until none of `sensorIds` appear in the streams response,
   * or until `DELETED_STREAM_POLL_DELAYS_MS` is exhausted. Updates `streams`
   * on every poll. Callers should keep the delete dialog open until this
   * resolves (NVBug 6243148).
   */
  waitUntilStreamsRemoved: (sensorIds: string[]) => Promise<WaitUntilStreamsRemovedResult>;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function useStreams({ vstApiUrl }: UseStreamsOptions = {}): UseStreamsResult {
  const [streams, setStreams] = useState<StreamInfo[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  /** Resolves to `null` when the listing could not be read — never confuse that with "VST has no streams". */
  const fetchStreams = useCallback(async ({ silent = false }: { silent?: boolean } = {}): Promise<StreamInfo[] | null> => {
    if (!vstApiUrl) {
      setError('VST API URL not configured');
      setIsLoading(false);
      return null;
    }

    const apiEndpoints = createApiEndpoints(vstApiUrl);
    if (!silent) {
      setIsLoading(true);
      setError(null);
    }

    try {
      const response = await fetch(apiEndpoints.STREAMS);

      if (!response.ok) {
        throw new Error(`Failed to fetch streams: ${response.status}`);
      }

      const data: StreamsApiResponse = await response.json();
      const allStreams = parseStreamsResponse(data);
      if (isMountedRef.current) setStreams(allStreams);
      return allStreams;
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error('Error fetching streams:', err);
      // A background poll that misses shouldn't blank the grid — the next
      // user-triggered fetch surfaces the problem if it persists.
      if (!silent && isMountedRef.current) {
        setError(err instanceof Error ? err.message : 'Failed to fetch streams');
      }
      return null;
    } finally {
      if (!silent && isMountedRef.current) setIsLoading(false);
    }
  }, [vstApiUrl]);

  const refetch = useCallback(async () => {
    await fetchStreams();
  }, [fetchStreams]);

  const waitUntilStreamsRemoved = useCallback(async (sensorIds: string[]): Promise<WaitUntilStreamsRemovedResult> => {
    if (sensorIds.length === 0) return { remainingSensorIds: [] };

    const pending = new Set(sensorIds);

    const dropGone = (listed: StreamInfo[] | null) => {
      // A poll that failed says nothing about what VST still holds. Reading it as
      // an empty list would confirm a deletion that may not have happened.
      if (listed === null) return;
      const present = new Set(listed.map((s) => s.sensorId));
      for (const sensorId of Array.from(pending)) {
        if (!present.has(sensorId)) pending.delete(sensorId);
      }
    };

    // Immediate check — VST may already have dropped them by the time the
    // agent delete returned.
    dropGone(await fetchStreams({ silent: true }));
    if (pending.size === 0) return { remainingSensorIds: [] };

    for (const pollDelay of DELETED_STREAM_POLL_DELAYS_MS) {
      await sleep(pollDelay);
      if (!isMountedRef.current) return { remainingSensorIds: Array.from(pending) };

      dropGone(await fetchStreams({ silent: true }));
      if (pending.size === 0) return { remainingSensorIds: [] };
    }

    return { remainingSensorIds: Array.from(pending) };
  }, [fetchStreams]);

  useEffect(() => {
    fetchStreams();
  }, [fetchStreams]);

  return {
    streams,
    isLoading,
    error,
    refetch,
    waitUntilStreamsRemoved,
  };
}

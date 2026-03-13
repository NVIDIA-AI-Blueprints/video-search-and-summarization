// SPDX-License-Identifier: MIT
/**
 * Custom React hook for managing search data fetching and state
 * 
 * This hook provides comprehensive search data management including API calls,
 * sensor mapping, error handling, and real-time data synchronization with
 * configurable time windows and verification filters.
 *
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { SearchData } from '../types';
import { SearchParams } from '../types';
import { formatDateToLocalISO } from '../utils/Formatter';

/**
 * Configuration options for the useSearch hook
 */
interface UseSearchOptions {
  agentApiUrl?: string;
  params?: SearchParams;
}

/**
 * Custom React hook for managing search data fetching and state management
 *
 */
export const useSearch = ({ agentApiUrl, params = {} }: UseSearchOptions) => {
  const [searchResults, setSearchResults] = useState<SearchData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchParams, setSearchParams] = useState<SearchParams>(params);
  
  // AbortController ref for canceling ongoing requests
  const abortControllerRef = useRef<AbortController | null>(null);

  // Cancel the current search request
  const cancelSearch = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
      setLoading(false);
    }
  }, []);

  const fetchSearch = useCallback(async () => {
    if (!agentApiUrl) {
      setError('Agent API URL is not configured. Please set NEXT_PUBLIC_AGENT_API_URL_BASE in your environment.');
      setLoading(false);
      return;
    }
    
    // Cancel any ongoing request before starting a new one
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    
    // Create a new AbortController for this request
    abortControllerRef.current = new AbortController();
    const { signal } = abortControllerRef.current;
    
    try {
      const { query, startDate, endDate, videoSources, similarity, topK = 10, agentMode = false, sourceType = 'video_file' } = searchParams;
      let body = {};
      let data = {data: []};
      if(!query) {
        setSearchResults([]);
        setLoading(false);
        return;
      }
      if(agentMode) {
        body = {
          agent_mode: agentMode,
          query: query,
          top_k: topK,
          source_type: sourceType
        }
      } else {
        body = {
          query: query,
          video_sources: videoSources || [],
          timestamp_start: formatDateToLocalISO(startDate || null),
          timestamp_end: formatDateToLocalISO(endDate || null),
          min_cosine_similarity: Number(similarity)?.toFixed(2),
          top_k: topK,
          agent_mode: agentMode,
          source_type: sourceType
        }
      }
      setLoading(true);
      setError(null);
      setSearchResults([]);

      const response = await fetch(`${agentApiUrl}/search`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
        signal, // Pass the abort signal to fetch
      });
      
      if (!response.ok) {
        // Try to get error details from response body
        let errorMessage = `HTTP error! status: ${response.status}`;
        try {
          const errorBody = await response.text();
          if (errorBody) {
            errorMessage = `${errorMessage}\n\nResponse:\n${errorBody}`;
          }
        } catch {
          // Ignore if can't read response body
        }
        throw new Error(errorMessage);
      }
      data = await response.json();
      
      // Transform API response to SearchData format
      const transformedSearchResults: SearchData[] = (data.data || []).map((searchResult: any) => ({
        video_name: searchResult.video_name || '',
        similarity: searchResult.similarity || 0,
        screenshot_url: searchResult.screenshot_url || '',
        description: searchResult.description || '',
        start_time: searchResult.start_time || '',
        end_time: searchResult.end_time || '',
        sensor_id: searchResult.sensor_id || '',
        object_ids: searchResult.object_ids || [],
      }));
      
      setSearchResults(transformedSearchResults);
    } catch (err) {
      // Don't set error if the request was aborted (cancelled by user)
      if (err instanceof Error && err.name === 'AbortError') {
        console.log('Search request was cancelled');
        return;
      }
      setError(err instanceof Error ? err.message : 'Failed to fetch search');
      console.error('Error fetching search:', err);
    } finally {
      setLoading(false);
    }
  }, [agentApiUrl, searchParams]);

  const fetchData = useCallback(async () => {
    await fetchSearch();
  }, [fetchSearch]);

  useEffect(() => {
    fetchData();
  }, [fetchData, searchParams]);
  
  const clearSearchResults = useCallback(() => {
    setSearchResults([]);
    setError(null);
  }, []);

  return {
    searchResults,
    loading,
    error,
    refetch: fetchSearch,
    onUpdateSearchParams: setSearchParams,
    cancelSearch,
    clearSearchResults,
  };
};

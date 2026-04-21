// SPDX-License-Identifier: MIT
import { renderHook, act, waitFor } from '@testing-library/react';
import { useSearch } from '../../lib-src/hooks/useSearch';

const mockFetchResponse = (data: any, ok = true, status = 200) =>
  jest.fn().mockResolvedValue({
    ok,
    status,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
  });

describe('useSearch', () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    jest.restoreAllMocks();
  });

  it('sets error when agentApiUrl is not provided', async () => {
    global.fetch = mockFetchResponse({});
    const { result } = renderHook(() => useSearch({ agentApiUrl: undefined }));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.error).toContain('Agent API URL is not configured');
    expect(result.current.searchResults).toEqual([]);
  });

  it('does not fetch when query is empty', async () => {
    const fetchSpy = mockFetchResponse({});
    global.fetch = fetchSpy;

    const { result } = renderHook(() =>
      useSearch({ agentApiUrl: 'http://api.test', params: { query: '' } })
    );

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(result.current.searchResults).toEqual([]);
  });

  it('fetches search results in normal mode', async () => {
    const apiResponse = {
      data: [
        {
          video_name: 'test.mp4',
          similarity: 0.85,
          screenshot_url: 'http://img.test/thumb.jpg',
          description: 'A person',
          start_time: '2024-01-01T00:00:00',
          end_time: '2024-01-01T00:05:00',
          sensor_id: 'sensor-1',
          object_ids: ['1'],
        },
      ],
    };
    global.fetch = mockFetchResponse(apiResponse);

    const { result } = renderHook(() =>
      useSearch({
        agentApiUrl: 'http://api.test',
        params: { query: 'person walking', agentMode: false, topK: 5 },
      })
    );

    await waitFor(() => {
      expect(result.current.searchResults).toHaveLength(1);
    });

    expect(global.fetch).toHaveBeenCalledWith(
      'http://api.test/search',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const body = JSON.parse((global.fetch as jest.Mock).mock.calls[0][1].body);
    expect(body.query).toBe('person walking');
    expect(body.agent_mode).toBe(false);
    expect(body.top_k).toBe(5);

    expect(result.current.searchResults[0].video_name).toBe('test.mp4');
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it('fetches search results in agent mode', async () => {
    global.fetch = mockFetchResponse({ data: [] });

    renderHook(() =>
      useSearch({
        agentApiUrl: 'http://api.test',
        params: { query: 'find cars', agentMode: true, topK: 10, sourceType: 'rtsp' },
      })
    );

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    const body = JSON.parse((global.fetch as jest.Mock).mock.calls[0][1].body);
    expect(body.agent_mode).toBe(true);
    expect(body.query).toBe('find cars');
    expect(body.source_type).toBe('rtsp');
    expect(body.video_sources).toBeUndefined();
    expect(body.timestamp_start).toBeUndefined();
  });

  it('handles HTTP error responses', async () => {
    global.fetch = mockFetchResponse({ error: 'Server error' }, false, 500);
    const consoleSpy = jest.spyOn(console, 'error').mockImplementation();

    const { result } = renderHook(() =>
      useSearch({
        agentApiUrl: 'http://api.test',
        params: { query: 'test query' },
      })
    );

    await waitFor(() => {
      expect(result.current.error).toContain('HTTP error');
    });

    expect(result.current.loading).toBe(false);
    consoleSpy.mockRestore();
  });

  it('handles network errors', async () => {
    global.fetch = jest.fn().mockRejectedValue(new Error('Network failure'));
    const consoleSpy = jest.spyOn(console, 'error').mockImplementation();

    const { result } = renderHook(() =>
      useSearch({
        agentApiUrl: 'http://api.test',
        params: { query: 'test query' },
      })
    );

    await waitFor(() => {
      expect(result.current.error).toBe('Network failure');
    });

    expect(result.current.loading).toBe(false);
    consoleSpy.mockRestore();
  });

  it('ignores AbortError when request is cancelled', async () => {
    const abortError = new DOMException('The operation was aborted.', 'AbortError');
    global.fetch = jest.fn().mockRejectedValue(abortError);
    const consoleSpy = jest.spyOn(console, 'log').mockImplementation();

    const { result } = renderHook(() =>
      useSearch({
        agentApiUrl: 'http://api.test',
        params: { query: 'test query' },
      })
    );

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.error).toBeNull();
    consoleSpy.mockRestore();
  });

  it('cancelSearch aborts the current request', async () => {
    global.fetch = jest.fn().mockImplementation(
      () => new Promise(() => { /* never resolves */ })
    );

    const { result } = renderHook(() =>
      useSearch({
        agentApiUrl: 'http://api.test',
        params: { query: 'test query' },
      })
    );

    // Wait for fetch to be called
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    act(() => {
      result.current.cancelSearch();
    });

    expect(result.current.loading).toBe(false);
  });

  it('clearSearchResults resets results and error', async () => {
    global.fetch = jest.fn().mockRejectedValue(new Error('fail'));
    const consoleSpy = jest.spyOn(console, 'error').mockImplementation();

    const { result } = renderHook(() =>
      useSearch({
        agentApiUrl: 'http://api.test',
        params: { query: 'test' },
      })
    );

    await waitFor(() => {
      expect(result.current.error).toBe('fail');
    });

    act(() => {
      result.current.clearSearchResults();
    });

    expect(result.current.searchResults).toEqual([]);
    expect(result.current.error).toBeNull();
    consoleSpy.mockRestore();
  });

  it('transforms response with missing fields using defaults', async () => {
    global.fetch = mockFetchResponse({
      data: [{ video_name: 'partial.mp4' }],
    });

    const { result } = renderHook(() =>
      useSearch({
        agentApiUrl: 'http://api.test',
        params: { query: 'partial' },
      })
    );

    await waitFor(() => {
      expect(result.current.searchResults).toHaveLength(1);
    });

    expect(result.current.searchResults[0]).toEqual({
      video_name: 'partial.mp4',
      similarity: 0,
      screenshot_url: '',
      description: '',
      start_time: '',
      end_time: '',
      sensor_id: '',
      object_ids: [],
    });
  });

  it('updates search when params change via onUpdateSearchParams', async () => {
    global.fetch = mockFetchResponse({ data: [] });

    const { result } = renderHook(() =>
      useSearch({ agentApiUrl: 'http://api.test', params: { query: '' } })
    );

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(global.fetch).not.toHaveBeenCalled();

    act(() => {
      result.current.onUpdateSearchParams({ query: 'new search' });
    });

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });
  });
});

// SPDX-License-Identifier: MIT
import { renderHook, act } from '@testing-library/react';
import { useVideoModal } from '../../lib-src/hooks/useVideoModal';
import { SearchData } from '../../lib-src/types';

const makeSearchData = (overrides: Partial<SearchData> = {}): SearchData => ({
  video_name: 'test-video.mp4',
  similarity: 0.9,
  screenshot_url: 'http://example.com/thumb.jpg',
  description: 'Test video',
  start_time: '2024-01-15T09:00:00',
  end_time: '2024-01-15T09:05:00',
  sensor_id: 'sensor-001',
  object_ids: ['obj-1', 'obj-2'],
  ...overrides,
});

const mockFetchResponse = (data: any, ok = true, status = 200) =>
  jest.fn().mockResolvedValue({
    ok,
    status,
    json: () => Promise.resolve(data),
  });

describe('useVideoModal', () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('initializes with closed modal state', () => {
    const { result } = renderHook(() => useVideoModal('http://vst.test'));

    expect(result.current.videoModal).toEqual({
      isOpen: false,
      videoUrl: '',
      title: '',
    });
  });

  it('opens modal with video URL after successful fetch', async () => {
    global.fetch = mockFetchResponse({ videoUrl: 'http://stream.test/video.mp4' });

    const { result } = renderHook(() => useVideoModal('http://vst.test'));

    await act(async () => {
      await result.current.openVideoModal(makeSearchData());
    });

    expect(result.current.videoModal).toEqual({
      isOpen: true,
      videoUrl: 'http://stream.test/video.mp4',
      title: 'test-video.mp4',
    });
  });

  it('builds correct fetch URL with query params', async () => {
    global.fetch = mockFetchResponse({ videoUrl: 'http://stream.test/video.mp4' });

    const { result } = renderHook(() => useVideoModal('http://vst.test'));

    await act(async () => {
      await result.current.openVideoModal(makeSearchData());
    });

    const calledUrl = (global.fetch as jest.Mock).mock.calls[0][0];
    expect(calledUrl).toContain('http://vst.test/v1/storage/file/sensor-001/url');
    expect(calledUrl).toContain('startTime=2024-01-15T09%3A00%3A00');
    expect(calledUrl).toContain('endTime=2024-01-15T09%3A05%3A00');
    expect(calledUrl).toContain('expiryMinutes=60');
    expect(calledUrl).toContain('container=mp4');
    expect(calledUrl).toContain('disableAudio=true');
  });

  it('includes bbox configuration when showObjectsBbox is true and object_ids exist', async () => {
    global.fetch = mockFetchResponse({ videoUrl: 'http://stream.test/video.mp4' });

    const { result } = renderHook(() => useVideoModal('http://vst.test'));

    await act(async () => {
      await result.current.openVideoModal(makeSearchData({ object_ids: ['1', '2'] }), true);
    });

    const calledUrl = (global.fetch as jest.Mock).mock.calls[0][0];
    expect(calledUrl).toContain('configuration=');
    const url = new URL(calledUrl);
    const config = JSON.parse(url.searchParams.get('configuration')!);
    expect(config.overlay.bbox.objectId).toEqual(['1', '2']);
    expect(config.overlay.bbox.showObjId).toBe(true);
    expect(config.overlay.color).toBe('red');
  });

  it('does not include bbox config when showObjectsBbox is false', async () => {
    global.fetch = mockFetchResponse({ videoUrl: 'http://stream.test/video.mp4' });

    const { result } = renderHook(() => useVideoModal('http://vst.test'));

    await act(async () => {
      await result.current.openVideoModal(makeSearchData(), false);
    });

    const calledUrl = (global.fetch as jest.Mock).mock.calls[0][0];
    expect(calledUrl).not.toContain('configuration=');
  });

  it('does not include bbox config when object_ids is empty', async () => {
    global.fetch = mockFetchResponse({ videoUrl: 'http://stream.test/video.mp4' });

    const { result } = renderHook(() => useVideoModal('http://vst.test'));

    await act(async () => {
      await result.current.openVideoModal(makeSearchData({ object_ids: [] }), true);
    });

    const calledUrl = (global.fetch as jest.Mock).mock.calls[0][0];
    expect(calledUrl).not.toContain('configuration=');
  });

  it('closes modal and resets state', async () => {
    global.fetch = mockFetchResponse({ videoUrl: 'http://stream.test/video.mp4' });

    const { result } = renderHook(() => useVideoModal('http://vst.test'));

    await act(async () => {
      await result.current.openVideoModal(makeSearchData());
    });
    expect(result.current.videoModal.isOpen).toBe(true);

    act(() => {
      result.current.closeVideoModal();
    });

    expect(result.current.videoModal).toEqual({
      isOpen: false,
      videoUrl: '',
      title: '',
    });
  });

  it('handles HTTP error responses', async () => {
    global.fetch = mockFetchResponse(null, false, 404);
    const consoleSpy = jest.spyOn(console, 'error').mockImplementation();

    const { result } = renderHook(() => useVideoModal('http://vst.test'));

    await act(async () => {
      await result.current.openVideoModal(makeSearchData());
    });

    expect(result.current.videoModal.isOpen).toBe(false);
    consoleSpy.mockRestore();
  });

  it('ignores AbortError silently', async () => {
    const abortError = new DOMException('Aborted', 'AbortError');
    global.fetch = jest.fn().mockRejectedValue(abortError);
    const consoleSpy = jest.spyOn(console, 'error').mockImplementation();

    const { result } = renderHook(() => useVideoModal('http://vst.test'));

    await act(async () => {
      await result.current.openVideoModal(makeSearchData());
    });

    expect(result.current.videoModal.isOpen).toBe(false);
    expect(consoleSpy).not.toHaveBeenCalled();
    consoleSpy.mockRestore();
  });

  it('aborts previous request when opening a new video', async () => {
    let callCount = 0;
    global.fetch = jest.fn().mockImplementation((_url: string, opts: any) => {
      callCount++;
      if (callCount === 1) {
        return new Promise((_, reject) => {
          opts.signal.addEventListener('abort', () =>
            reject(new DOMException('Aborted', 'AbortError'))
          );
        });
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ videoUrl: 'http://stream.test/second.mp4' }),
      });
    });

    const consoleSpy = jest.spyOn(console, 'error').mockImplementation();
    const { result } = renderHook(() => useVideoModal('http://vst.test'));

    await act(async () => {
      result.current.openVideoModal(makeSearchData({ video_name: 'first.mp4' }));
      await result.current.openVideoModal(makeSearchData({ video_name: 'second.mp4' }));
    });

    expect(result.current.videoModal.title).toBe('second.mp4');
    consoleSpy.mockRestore();
  });
});

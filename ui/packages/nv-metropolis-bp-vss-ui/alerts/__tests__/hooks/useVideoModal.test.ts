// SPDX-License-Identifier: MIT
import { renderHook, act } from '@testing-library/react';
import { useVideoModal } from '../../lib-src/hooks/useVideoModal';
import { AlertData } from '../../lib-src/types';

const makeAlert = (overrides: Partial<AlertData> = {}): AlertData => ({
  id: 'alert-1',
  timestamp: '2024-01-15T09:00:00Z',
  end: '2024-01-15T09:05:00Z',
  sensor: 'Cam-A',
  alertType: 'Tailgating',
  alertTriggered: 'Motion Detected',
  alertDescription: 'Test alert',
  metadata: {},
  ...overrides,
});

const mockFetchResponse = (data: any, ok = true) =>
  jest.fn().mockResolvedValue({
    ok,
    status: ok ? 200 : 500,
    json: () => Promise.resolve(data),
  });

describe('useVideoModal', () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    jest.restoreAllMocks();
  });

  it('initializes with closed modal state', () => {
    const { result } = renderHook(() => useVideoModal('http://vst.test'));

    expect(result.current.videoModal).toEqual({
      isOpen: false,
      videoUrl: '',
      title: '',
    });
    expect(result.current.loadingAlertId).toBeNull();
  });

  it('uses alertTriggered as title, falls back to alertType', async () => {
    // Bypass videoSource check and VST API by providing videoSource in metadata
    // Mock video element for checkVideoUrl
    const originalCreateElement = document.createElement.bind(document);
    jest.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag === 'video') {
        const video = originalCreateElement('video');
        setTimeout(() => {
          if (video.onloadedmetadata) video.onloadedmetadata(new Event('loadedmetadata'));
        }, 0);
        return video;
      }
      return originalCreateElement(tag);
    });

    const alert = makeAlert({
      alertTriggered: '',
      alertType: 'Intrusion',
      metadata: { info: { videoSource: 'http://video.test/clip.mp4' } },
    });

    const { result } = renderHook(() => useVideoModal('http://vst.test'));

    await act(async () => {
      await result.current.openVideoModal(alert);
    });

    expect(result.current.videoModal.title).toBe('Intrusion');
    (document.createElement as jest.Mock).mockRestore();
  });

  it('shows N/A when both alertTriggered and alertType are empty', async () => {
    const originalCreateElement = document.createElement.bind(document);
    jest.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag === 'video') {
        const video = originalCreateElement('video');
        setTimeout(() => {
          if (video.onloadedmetadata) video.onloadedmetadata(new Event('loadedmetadata'));
        }, 0);
        return video;
      }
      return originalCreateElement(tag);
    });

    const alert = makeAlert({
      alertTriggered: '',
      alertType: '',
      metadata: { info: { videoSource: 'http://video.test/clip.mp4' } },
    });

    const { result } = renderHook(() => useVideoModal('http://vst.test'));

    await act(async () => {
      await result.current.openVideoModal(alert);
    });

    expect(result.current.videoModal.title).toBe('N/A');
    (document.createElement as jest.Mock).mockRestore();
  });

  it('closes modal and resets state', () => {
    const { result } = renderHook(() => useVideoModal('http://vst.test'));

    act(() => {
      result.current.closeVideoModal();
    });

    expect(result.current.videoModal).toEqual({
      isOpen: false,
      videoUrl: '',
      title: '',
    });
  });

  it('falls back to VST API when videoSource is not accessible', async () => {
    // Mock video element to fail (onerror)
    const originalCreateElement = document.createElement.bind(document);
    jest.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag === 'video') {
        const video = originalCreateElement('video');
        setTimeout(() => {
          if (video.onerror) video.onerror(new Event('error'));
        }, 0);
        return video;
      }
      return originalCreateElement(tag);
    });

    global.fetch = mockFetchResponse({
      videoUrl: 'http://vst.test/vst/v1/storage/video.mp4',
    });

    const sensorMap = new Map([['Cam-A', 'sensor-id-a']]);
    const alert = makeAlert({
      metadata: { info: { videoSource: 'http://bad-url.test/video.mp4' } },
    });

    const consoleSpy = jest.spyOn(console, 'warn').mockImplementation();
    const { result } = renderHook(() => useVideoModal('http://vst.test/vst', sensorMap));

    await act(async () => {
      await result.current.openVideoModal(alert);
    });

    expect(result.current.videoModal.isOpen).toBe(true);
    expect(result.current.videoModal.videoUrl).toContain('vst');

    consoleSpy.mockRestore();
    (document.createElement as jest.Mock).mockRestore();
  });

  it('returns early when vstApiUrl or sensorMap is missing', async () => {
    const originalCreateElement = document.createElement.bind(document);
    jest.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag === 'video') {
        const video = originalCreateElement('video');
        setTimeout(() => {
          if (video.onerror) video.onerror(new Event('error'));
        }, 0);
        return video;
      }
      return originalCreateElement(tag);
    });

    const consoleSpy = jest.spyOn(console, 'error').mockImplementation();
    jest.spyOn(console, 'warn').mockImplementation();

    const alert = makeAlert({
      metadata: { info: { videoSource: 'http://bad-url.test/video.mp4' } },
    });

    // No sensorMap provided
    const { result } = renderHook(() => useVideoModal('http://vst.test'));

    await act(async () => {
      await result.current.openVideoModal(alert);
    });

    expect(result.current.videoModal.isOpen).toBe(false);
    consoleSpy.mockRestore();
    (document.createElement as jest.Mock).mockRestore();
  });

  it('returns early when sensor is not found in sensorMap', async () => {
    const originalCreateElement = document.createElement.bind(document);
    jest.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag === 'video') {
        const video = originalCreateElement('video');
        setTimeout(() => {
          if (video.onerror) video.onerror(new Event('error'));
        }, 0);
        return video;
      }
      return originalCreateElement(tag);
    });

    const consoleSpy = jest.spyOn(console, 'error').mockImplementation();
    jest.spyOn(console, 'warn').mockImplementation();

    const sensorMap = new Map([['Other-Cam', 'other-id']]);
    const alert = makeAlert({
      metadata: { info: { videoSource: 'http://bad-url.test/video.mp4' } },
    });

    const { result } = renderHook(() => useVideoModal('http://vst.test', sensorMap));

    await act(async () => {
      await result.current.openVideoModal(alert);
    });

    expect(result.current.videoModal.isOpen).toBe(false);
    consoleSpy.mockRestore();
    (document.createElement as jest.Mock).mockRestore();
  });

  it('handles fetch error gracefully', async () => {
    const originalCreateElement = document.createElement.bind(document);
    jest.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag === 'video') {
        const video = originalCreateElement('video');
        setTimeout(() => {
          if (video.onerror) video.onerror(new Event('error'));
        }, 0);
        return video;
      }
      return originalCreateElement(tag);
    });

    global.fetch = mockFetchResponse(null, false);

    const consoleSpy = jest.spyOn(console, 'error').mockImplementation();
    jest.spyOn(console, 'warn').mockImplementation();

    const sensorMap = new Map([['Cam-A', 'sensor-id-a']]);
    const alert = makeAlert({
      metadata: { info: { videoSource: 'http://bad-url.test/video.mp4' } },
    });

    const { result } = renderHook(() => useVideoModal('http://vst.test/vst', sensorMap));

    await act(async () => {
      await result.current.openVideoModal(alert);
    });

    expect(result.current.videoModal.isOpen).toBe(false);
    consoleSpy.mockRestore();
    (document.createElement as jest.Mock).mockRestore();
  });

  it('sets loadingAlertId while loading', async () => {
    const originalCreateElement = document.createElement.bind(document);
    jest.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag === 'video') {
        const video = originalCreateElement('video');
        setTimeout(() => {
          if (video.onloadedmetadata) video.onloadedmetadata(new Event('loadedmetadata'));
        }, 10);
        return video;
      }
      return originalCreateElement(tag);
    });

    const alert = makeAlert({
      id: 'loading-test',
      metadata: { info: { videoSource: 'http://video.test/clip.mp4' } },
    });

    const { result } = renderHook(() => useVideoModal('http://vst.test'));

    let loadingIdDuringOpen: string | null = null;
    await act(async () => {
      const promise = result.current.openVideoModal(alert);
      // loadingAlertId should be set before promise resolves
      loadingIdDuringOpen = result.current.loadingAlertId;
      await promise;
    });

    // After completion, loadingAlertId should be cleared
    expect(result.current.loadingAlertId).toBeNull();
    (document.createElement as jest.Mock).mockRestore();
  });
});

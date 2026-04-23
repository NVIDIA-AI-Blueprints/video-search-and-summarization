// SPDX-License-Identifier: MIT
import { renderHook, act } from '@testing-library/react';
import { useVideoModal, type VideoModalData, type AlertLike } from '../../lib-src/hooks/useVideoModal';
import { mockFetchResponse } from '../../test-helpers';

const makeSearchData = (overrides: Partial<VideoModalData> = {}): VideoModalData => ({
  video_name: 'test-video.mp4',
  start_time: '2024-01-15T09:00:00',
  end_time: '2024-01-15T09:05:00',
  sensor_id: 'sensor-001',
  object_ids: ['obj-1'],
  ...overrides,
});

const makeAlert = (overrides: Partial<AlertLike> = {}): AlertLike => ({
  id: 'alert-1',
  timestamp: '2024-01-15T09:00:00Z',
  end: '2024-01-15T09:05:00Z',
  sensor: 'Cam-A',
  alertType: 'Intrusion',
  metadata: {},
  ...overrides,
});

describe('useVideoModal', () => {
  beforeEach(() => {
    HTMLVideoElement.prototype.load = jest.fn();
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  describe('initial state', () => {
    it('initializes with closed modal', () => {
      const { result } = renderHook(() => useVideoModal('http://vst.test'));

      expect(result.current.videoModal).toEqual({
        isOpen: false,
        videoUrl: '',
        title: '',
      });
      expect(result.current.loadingAlertId).toBeNull();
    });
  });

  describe('openVideoModalFromUrl', () => {
    it('opens modal with given url and title', () => {
      const { result } = renderHook(() => useVideoModal());

      act(() => {
        result.current.openVideoModalFromUrl('My Video', 'http://example.com/video.mp4');
      });

      expect(result.current.videoModal).toEqual({
        isOpen: true,
        videoUrl: 'http://example.com/video.mp4',
        title: 'My Video',
      });
    });
  });

  describe('closeVideoModal', () => {
    it('closes modal and resets state', () => {
      const { result } = renderHook(() => useVideoModal());

      act(() => {
        result.current.openVideoModalFromUrl('Title', 'http://url');
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
  });

  describe('openVideoModal', () => {
    let originalFetch: typeof global.fetch;

    beforeEach(() => {
      originalFetch = global.fetch;
    });

    afterEach(() => {
      global.fetch = originalFetch;
    });

    it('does nothing when vstApiUrl is undefined', async () => {
      const consoleSpy = jest.spyOn(console, 'error').mockImplementation();
      const { result } = renderHook(() => useVideoModal(undefined));

      await act(async () => {
        await result.current.openVideoModal(makeSearchData());
      });

      expect(result.current.videoModal.isOpen).toBe(false);
      expect(consoleSpy).toHaveBeenCalledWith('VST API URL not available');
      consoleSpy.mockRestore();
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
  });

  describe('openVideoModalFromAlert', () => {
    let originalFetch: typeof global.fetch;

    beforeEach(() => {
      originalFetch = global.fetch;
    });

    afterEach(() => {
      global.fetch = originalFetch;
    });

    it('opens modal from videoSource when accessible', async () => {
      const originalCreateElement = document.createElement.bind(document);
      jest.spyOn(document, 'createElement').mockImplementation((tag: string) => {
        if (tag === 'video') {
          const video = originalCreateElement('video') as HTMLVideoElement;
          setTimeout(() => {
            if (video.onloadedmetadata) video.onloadedmetadata(new Event('loadedmetadata'));
          }, 0);
          return video;
        }
        return originalCreateElement(tag);
      });

      const sensorMap = new Map([['Cam-A', 'sensor-id']]);
      const { result } = renderHook(() =>
        useVideoModal('http://vst.test', { sensorMap })
      );

      const alert = makeAlert({
        metadata: { info: { videoSource: 'http://direct.example.com/clip.mp4' } },
      });

      await act(async () => {
        await result.current.openVideoModalFromAlert(alert);
      });

      expect(result.current.videoModal.videoUrl).toBe('http://direct.example.com/clip.mp4');
      expect(result.current.loadingAlertId).toBeNull();
    });

    it('falls back to VST API when videoSource not accessible', async () => {
      const originalCreateElement = document.createElement.bind(document);
      jest.spyOn(document, 'createElement').mockImplementation((tag: string) => {
        if (tag === 'video') {
          const video = originalCreateElement('video') as HTMLVideoElement;
          setTimeout(() => {
            if (video.onerror) video.onerror(new Event('error'));
          }, 0);
          return video;
        }
        return originalCreateElement(tag);
      });

      global.fetch = mockFetchResponse({ videoUrl: 'http://vst.test/vst/storage/clip.mp4' });
      const sensorMap = new Map([['Cam-A', 'sensor-id']]);
      const { result } = renderHook(() =>
        useVideoModal('http://vst.test/vst/api', { sensorMap })
      );

      const alert = makeAlert({
        metadata: { info: { videoSource: 'http://inaccessible.example.com/clip.mp4' } },
      });

      await act(async () => {
        await result.current.openVideoModalFromAlert(alert);
      });

      expect(result.current.videoModal.videoUrl).toContain('vst');
      expect(result.current.loadingAlertId).toBeNull();
    });

    it('does nothing when vstApiUrl or sensorMap missing', async () => {
      const consoleSpy = jest.spyOn(console, 'error').mockImplementation();
      const { result } = renderHook(() =>
        useVideoModal('http://vst.test', { sensorMap: undefined })
      );

      await act(async () => {
        await result.current.openVideoModalFromAlert(makeAlert());
      });

      expect(result.current.videoModal.isOpen).toBe(false);
      expect(consoleSpy).toHaveBeenCalledWith('VST API URL or sensor map not available');
      consoleSpy.mockRestore();
    });

    it('uses alertTriggered as title, falls back to alertType', async () => {
      global.fetch = mockFetchResponse({ videoUrl: 'http://stream.test/video.mp4' });
      const sensorMap = new Map([['Cam-A', 'sensor-id']]);
      const { result } = renderHook(() =>
        useVideoModal('http://vst.test', { sensorMap })
      );

      await act(async () => {
        await result.current.openVideoModalFromAlert(
          makeAlert({ alertTriggered: 'Motion', alertType: 'Intrusion' })
        );
      });

      expect(result.current.videoModal.title).toBe('Motion');
    });
  });
});

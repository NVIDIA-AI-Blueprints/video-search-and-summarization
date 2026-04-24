// SPDX-License-Identifier: MIT
import {
  replaceVideoUrlBase,
  fetchVideoUrlFromVst,
  checkVideoUrl,
  type FetchVideoUrlParams,
} from '../../lib-src/utils/videoModal';
import { mockFetchResponse } from '../../test-helpers';

describe('replaceVideoUrlBase', () => {
  let consoleErrorSpy: jest.SpyInstance;
  let consoleWarnSpy: jest.SpyInstance;

  beforeEach(() => {
    consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation();
    consoleWarnSpy = jest.spyOn(console, 'warn').mockImplementation();
  });

  afterEach(() => {
    consoleErrorSpy.mockRestore();
    consoleWarnSpy.mockRestore();
  });

  it('replaces base URL when both URLs contain /vst', () => {
    const result = replaceVideoUrlBase(
      'http://other-host:30888/vst/storage/xyz/segment.mp4?token=abc',
      'http://vst.test/vst/api'
    );
    expect(result).toBe('http://vst.test/vst/storage/xyz/segment.mp4?token=abc');
  });

  it('handles relative videoUrl', () => {
    const result = replaceVideoUrlBase(
      '/vst/storage/xyz/segment.mp4?token=abc',
      'http://vst.test/vst/api'
    );
    expect(result).toBe('http://vst.test/vst/storage/xyz/segment.mp4?token=abc');
  });

  it('returns videoUrl when vstApiUrl is empty', () => {
    const videoUrl = 'http://host/vst/path';
    expect(replaceVideoUrlBase(videoUrl, '')).toBe(videoUrl);
  });

  it('returns videoUrl when videoUrl is empty', () => {
    expect(replaceVideoUrlBase('', 'http://vst.test/vst/api')).toBe('');
  });

  it('returns videoUrl and logs error when /vst not found in vstApiUrl', () => {
    const videoUrl = 'http://stream.test/vst/storage/segment.mp4';
    const result = replaceVideoUrlBase(videoUrl, 'http://vst.test/api');

    expect(result).toBe(videoUrl);
    expect(consoleErrorSpy).toHaveBeenCalledWith(
      'Failed to replace video URL: /vst path segment not found in URLs',
      expect.objectContaining({ vstApiUrl: 'http://vst.test/api', videoUrl })
    );
  });

  it('returns videoUrl and logs error when /vst not found in videoUrl', () => {
    const videoUrl = 'http://stream.test/storage/segment.mp4';
    const result = replaceVideoUrlBase(videoUrl, 'http://vst.test/vst/api');

    expect(result).toBe(videoUrl);
    expect(consoleErrorSpy).toHaveBeenCalled();
  });

  it('falls back to original and warns when constructed URL is invalid', () => {
    const videoUrl = 'http://other.com/vst/segment.mp4';
    const result = replaceVideoUrlBase(videoUrl, '/vst/api');

    expect(result).toBe(videoUrl);
    expect(consoleWarnSpy).toHaveBeenCalled();
    expect(consoleWarnSpy.mock.calls[0][0]).toBe(
      'Constructed video URL is invalid, using original. Bad URL:'
    );
    expect(consoleWarnSpy.mock.calls[0][1]).toBe('/vst/segment.mp4');
    expect(consoleWarnSpy.mock.calls[0][3]).toBe(videoUrl);
  });

  it('preserves query string and hash in video path', () => {
    const result = replaceVideoUrlBase(
      'http://old/vst/path?foo=1#anchor',
      'http://new/vst/api'
    );
    expect(result).toBe('http://new/vst/path?foo=1#anchor');
  });
});

describe('fetchVideoUrlFromVst', () => {
  const defaultParams: FetchVideoUrlParams = {
    sensorId: 'sensor-001',
    startTime: '2024-01-15T09:00:00',
    endTime: '2024-01-15T09:05:00',
  };

  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    jest.restoreAllMocks();
  });

  it('returns videoUrl from API response', async () => {
    global.fetch = mockFetchResponse({ videoUrl: 'http://stream.test/video.mp4' });

    const result = await fetchVideoUrlFromVst('http://vst.test', defaultParams);

    expect(result).toBe('http://stream.test/video.mp4');
  });

  it('replaces video URL base with vstApiUrl base', async () => {
    global.fetch = mockFetchResponse({
      videoUrl: 'http://other-host:30888/vst/storage/xyz/segment.mp4?token=abc',
    });

    const result = await fetchVideoUrlFromVst(
      'http://vst.test/vst/api',
      defaultParams
    );

    expect(result).toBe('http://vst.test/vst/storage/xyz/segment.mp4?token=abc');
  });

  it('builds correct fetch URL with query params', async () => {
    global.fetch = mockFetchResponse({ videoUrl: 'http://stream.test/video.mp4' });

    await fetchVideoUrlFromVst('http://vst.test', defaultParams);

    const calledUrl = (global.fetch as jest.Mock).mock.calls[0][0];
    expect(calledUrl).toContain('http://vst.test/v1/storage/file/sensor-001/url');
    expect(calledUrl).toContain('startTime=');
    expect(calledUrl).toContain('endTime=');
    expect(calledUrl).toContain('expiryMinutes=60');
    expect(calledUrl).toContain('container=mp4');
    expect(calledUrl).toContain('disableAudio=true');
  });

  it('includes bbox configuration when showObjectsBbox is true and objectIds exist', async () => {
    global.fetch = mockFetchResponse({ videoUrl: 'http://stream.test/video.mp4' });

    await fetchVideoUrlFromVst('http://vst.test', {
      ...defaultParams,
      objectIds: ['1', '2'],
      showObjectsBbox: true,
    });

    const calledUrl = (global.fetch as jest.Mock).mock.calls[0][0];
    expect(calledUrl).toContain('configuration=');
    const url = new URL(calledUrl);
    const config = JSON.parse(url.searchParams.get('configuration')!);
    expect(config.overlay.bbox.objectId).toEqual(['1', '2']);
    expect(config.overlay.bbox.showObjId).toBe(true);
  });

  it('does not include bbox config when showObjectsBbox is false', async () => {
    global.fetch = mockFetchResponse({ videoUrl: 'http://stream.test/video.mp4' });

    await fetchVideoUrlFromVst('http://vst.test', {
      ...defaultParams,
      objectIds: ['1', '2'],
      showObjectsBbox: false,
    });

    const calledUrl = (global.fetch as jest.Mock).mock.calls[0][0];
    expect(calledUrl).not.toContain('configuration=');
  });

  it('does not include bbox config when objectIds is empty', async () => {
    global.fetch = mockFetchResponse({ videoUrl: 'http://stream.test/video.mp4' });

    await fetchVideoUrlFromVst('http://vst.test', {
      ...defaultParams,
      objectIds: [],
      showObjectsBbox: true,
    });

    const calledUrl = (global.fetch as jest.Mock).mock.calls[0][0];
    expect(calledUrl).not.toContain('configuration=');
  });

  it('throws on HTTP error', async () => {
    global.fetch = mockFetchResponse(null, false, 404);

    await expect(
      fetchVideoUrlFromVst('http://vst.test', defaultParams)
    ).rejects.toThrow('Failed to fetch video URL: 404');
  });

  it('returns empty string when API returns no videoUrl', async () => {
    global.fetch = mockFetchResponse({});

    const result = await fetchVideoUrlFromVst('http://vst.test', defaultParams);

    expect(result).toBe('');
  });

  it('passes AbortSignal to fetch', async () => {
    const controller = new AbortController();
    global.fetch = mockFetchResponse({ videoUrl: 'http://stream.test/video.mp4' });

    await fetchVideoUrlFromVst('http://vst.test', defaultParams, controller.signal);

    expect((global.fetch as jest.Mock).mock.calls[0][1].signal).toBe(
      controller.signal
    );
  });
});

describe('checkVideoUrl', () => {
  beforeEach(() => {
    HTMLVideoElement.prototype.load = jest.fn();
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('resolves true when video loads metadata', async () => {
    const originalCreateElement = document.createElement.bind(document);
    jest.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag === 'video') {
        const video = originalCreateElement('video') as HTMLVideoElement;
        video.load = jest.fn();
        setTimeout(() => {
          if (video.onloadedmetadata) video.onloadedmetadata(new Event('loadedmetadata'));
        }, 0);
        return video;
      }
      return originalCreateElement(tag);
    });

    const result = await checkVideoUrl('http://example.com/video.mp4');
    expect(result).toBe(true);
  });

  it('resolves false when video errors', async () => {
    const originalCreateElement = document.createElement.bind(document);
    jest.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag === 'video') {
        const video = originalCreateElement('video') as HTMLVideoElement;
        video.load = jest.fn();
        setTimeout(() => {
          if (video.onerror) video.onerror(new Event('error'));
        }, 0);
        return video;
      }
      return originalCreateElement(tag);
    });

    const result = await checkVideoUrl('http://example.com/bad.mp4');
    expect(result).toBe(false);
  });

  it('resolves false when signal is aborted', async () => {
    const controller = new AbortController();
    controller.abort();

    const result = await checkVideoUrl('http://example.com/video.mp4', controller.signal);
    expect(result).toBe(false);
  });

  it('resolves false on timeout', async () => {
    const originalCreateElement = document.createElement.bind(document);
    jest.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag === 'video') {
        const video = originalCreateElement('video') as HTMLVideoElement;
        video.load = jest.fn();
        return video;
      }
      return originalCreateElement(tag);
    });

    const result = await checkVideoUrl('http://example.com/slow.mp4', undefined, 50);
    expect(result).toBe(false);
  }, 200);
});

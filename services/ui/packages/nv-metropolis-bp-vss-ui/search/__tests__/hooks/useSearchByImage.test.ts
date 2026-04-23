// SPDX-License-Identifier: MIT
import { renderHook, act } from '@testing-library/react';
import { useSearchByImage } from '../../lib-src/hooks/useSearchByImage';

const MOCK_VST_URL = 'http://vst.test';
const MOCK_MDX_URL = 'http://mdx.test';

const mockBlob = new Blob(['fake-image'], { type: 'image/jpeg' });

const mockFetchResponse = (data: any, ok = true, status = 200) =>
  ({
    ok,
    status,
    json: () => Promise.resolve(data),
    blob: () => Promise.resolve(mockBlob),
  } as unknown as Response);

const mockFrameApiResponse = (objects: any[] = [], timestamp = '2025-01-01T00:01:00.000Z') => [
  { timestamp, metadata: { objects } },
];

const makeBboxObject = (id: string, coords = { leftX: 10, topY: 20, rightX: 100, bottomY: 200 }) => ({
  id,
  bbox: coords,
});

/**
 * Mock Image so that setting .src triggers onload synchronously via microtask.
 * This avoids real image decoding in jsdom.
 */
let imageInstances: any[] = [];
const OriginalImage = globalThis.Image;

beforeEach(() => {
  imageInstances = [];
  (globalThis as any).Image = class MockImage {
    width = 640;
    height = 480;
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    _src = '';
    constructor() { imageInstances.push(this); }
    set src(value: string) {
      this._src = value;
      Promise.resolve().then(() => this.onload?.());
    }
    get src() { return this._src; }
  };
});

afterEach(() => {
  globalThis.Image = OriginalImage;
});

describe('useSearchByImage', () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    jest.restoreAllMocks();
  });

  // ---------------------------------------------------------------------------
  // Initial state
  // ---------------------------------------------------------------------------

  it('initializes with inactive state', () => {
    const { result } = renderHook(() => useSearchByImage({ vstApiUrl: MOCK_VST_URL, mdxWebApiUrl: MOCK_MDX_URL }));

    expect(result.current.searchByImageActive).toBe(false);
    expect(result.current.searchByImageLoading).toBe(false);
    expect(result.current.searchByImageError).toBeNull();
    expect(result.current.searchByImageFrameData).toBeNull();
  });

  // ---------------------------------------------------------------------------
  // startSearchByImage - missing config
  // ---------------------------------------------------------------------------

  it('sets error when vstApiUrl is missing', async () => {
    const { result } = renderHook(() => useSearchByImage({ vstApiUrl: undefined, mdxWebApiUrl: MOCK_MDX_URL }));

    await act(async () => {
      await result.current.startSearchByImage('sensor-1', 'Camera-1', '2025-01-01T00:00:00.000Z', 10, 'http://example.com/video.mp4');
    });

    expect(result.current.searchByImageActive).toBe(false);
    expect(result.current.searchByImageError).toBe('VST API URL or MDX Web API URL not configured');
  });

  it('sets error when mdxWebApiUrl is missing', async () => {
    const { result } = renderHook(() => useSearchByImage({ vstApiUrl: MOCK_VST_URL, mdxWebApiUrl: undefined }));

    await act(async () => {
      await result.current.startSearchByImage('sensor-1', 'Camera-1', '2025-01-01T00:00:00.000Z', 10, 'http://example.com/video.mp4');
    });

    expect(result.current.searchByImageActive).toBe(false);
    expect(result.current.searchByImageError).toBe('VST API URL or MDX Web API URL not configured');
  });

  // ---------------------------------------------------------------------------
  // startSearchByImage - success flow
  // ---------------------------------------------------------------------------

  it('fetches frame image and metadata on startSearchByImage', async () => {
    const objects = [makeBboxObject('obj-1'), makeBboxObject('obj-2')];

    global.fetch = jest.fn()
      .mockResolvedValueOnce(mockFetchResponse(null))
      .mockResolvedValueOnce(mockFetchResponse(mockFrameApiResponse(objects)));

    const { result } = renderHook(() => useSearchByImage({ vstApiUrl: MOCK_VST_URL, mdxWebApiUrl: MOCK_MDX_URL }));

    await act(async () => {
      await result.current.startSearchByImage('sensor-1', 'Camera-1', '2025-01-01T00:00:00.000Z', 60, 'http://example.com/video.mp4');
    });

    expect(result.current.searchByImageActive).toBe(true);
    expect(result.current.searchByImageLoading).toBe(false);
    expect(result.current.searchByImageError).toBeNull();
    expect(result.current.searchByImageFrameData).not.toBeNull();
    expect(result.current.searchByImageFrameData?.objects).toHaveLength(2);
    expect(result.current.searchByImageFrameData?.sensorId).toBe('sensor-1');
    expect(result.current.searchByImageFrameData?.sensorName).toBe('Camera-1');
  });

  it('calls correct VST picture URL with sensorId and timestamp', async () => {
    const fetchSpy = jest.fn()
      .mockResolvedValueOnce(mockFetchResponse(null))
      .mockResolvedValueOnce(mockFetchResponse(mockFrameApiResponse([])));
    global.fetch = fetchSpy;

    const { result } = renderHook(() => useSearchByImage({ vstApiUrl: MOCK_VST_URL, mdxWebApiUrl: MOCK_MDX_URL }));

    await act(async () => {
      await result.current.startSearchByImage('sensor-1', 'Camera-1', '2025-01-01T00:00:00.000Z', 30, 'http://example.com/video.mp4');
    });

    const pictureCall = fetchSpy.mock.calls[0];
    expect(pictureCall[0]).toContain(`${MOCK_VST_URL}/v1/replay/stream/sensor-1/picture`);
    expect(pictureCall[0]).toContain('startTime=');
    expect(pictureCall[1].headers.streamId).toBe('sensor-1');
  });

  it('calls correct MDX frames URL with sensorName and timestamp range', async () => {
    const fetchSpy = jest.fn()
      .mockResolvedValueOnce(mockFetchResponse(null))
      .mockResolvedValueOnce(mockFetchResponse(mockFrameApiResponse([])));
    global.fetch = fetchSpy;

    const { result } = renderHook(() => useSearchByImage({ vstApiUrl: MOCK_VST_URL, mdxWebApiUrl: MOCK_MDX_URL }));

    await act(async () => {
      await result.current.startSearchByImage('sensor-1', 'Camera-1', '2025-01-01T00:00:00.000Z', 30, 'http://example.com/video.mp4');
    });

    const framesCall = fetchSpy.mock.calls[1];
    expect(framesCall[0]).toContain(`${MOCK_MDX_URL}/frames`);
    expect(framesCall[0]).toContain('sensorId=Camera-1');
    expect(framesCall[0]).toContain('fromTimestamp=');
    expect(framesCall[0]).toContain('toTimestamp=');
  });

  // ---------------------------------------------------------------------------
  // startSearchByImage - timestamp extraction from video URL
  // ---------------------------------------------------------------------------

  it('extracts startTime from video URL query params', async () => {
    const fetchSpy = jest.fn()
      .mockResolvedValueOnce(mockFetchResponse(null))
      .mockResolvedValueOnce(mockFetchResponse(mockFrameApiResponse([])));
    global.fetch = fetchSpy;

    const { result } = renderHook(() => useSearchByImage({ vstApiUrl: MOCK_VST_URL, mdxWebApiUrl: MOCK_MDX_URL }));

    const videoUrl = 'http://vst.test/video.mp4?startTime=2025-06-15T10:30:00.000Z&endTime=2025-06-15T10:31:00.000Z';
    await act(async () => {
      await result.current.startSearchByImage('sensor-1', 'Camera-1', '2025-01-01T00:00:00.000Z', 5, videoUrl);
    });

    const pictureUrl = fetchSpy.mock.calls[0][0];
    expect(pictureUrl).toContain('startTime=2025-06-15T10%3A30%3A05.000Z');
  });

  it('extracts startTime from video filename pattern', async () => {
    const fetchSpy = jest.fn()
      .mockResolvedValueOnce(mockFetchResponse(null))
      .mockResolvedValueOnce(mockFetchResponse(mockFrameApiResponse([])));
    global.fetch = fetchSpy;

    const { result } = renderHook(() => useSearchByImage({ vstApiUrl: MOCK_VST_URL, mdxWebApiUrl: MOCK_MDX_URL }));

    await act(async () => {
      await result.current.startSearchByImage('sensor-1', 'Camera-1', '2025-01-01T00:00:00.000Z', 10, '/videos/sample_20250615_103000_abc123.mp4');
    });

    const pictureUrl = fetchSpy.mock.calls[0][0];
    expect(pictureUrl).toContain('startTime=2025-06-15T10%3A30%3A10.000Z');
  });

  it('falls back to videoStartTime when URL has no extractable timestamp', async () => {
    const fetchSpy = jest.fn()
      .mockResolvedValueOnce(mockFetchResponse(null))
      .mockResolvedValueOnce(mockFetchResponse(mockFrameApiResponse([])));
    global.fetch = fetchSpy;

    const { result } = renderHook(() => useSearchByImage({ vstApiUrl: MOCK_VST_URL, mdxWebApiUrl: MOCK_MDX_URL }));

    await act(async () => {
      await result.current.startSearchByImage('sensor-1', 'Camera-1', '2025-01-01T00:00:00.000Z', 10, '/plain-video.mp4');
    });

    const pictureUrl = fetchSpy.mock.calls[0][0];
    expect(pictureUrl).toContain('startTime=2025-01-01T00%3A00%3A10.000Z');
  });

  // ---------------------------------------------------------------------------
  // startSearchByImage - invalid timestamp
  // ---------------------------------------------------------------------------

  it('sets error for invalid video start time', async () => {
    jest.spyOn(console, 'error').mockImplementation();
    global.fetch = jest.fn();

    const { result } = renderHook(() => useSearchByImage({ vstApiUrl: MOCK_VST_URL, mdxWebApiUrl: MOCK_MDX_URL }));

    await act(async () => {
      await result.current.startSearchByImage('sensor-1', 'Camera-1', 'not-a-date', 10, '/video.mp4');
    });

    expect(result.current.searchByImageLoading).toBe(false);
    expect(result.current.searchByImageError).toContain('Invalid video start time');
  });

  // ---------------------------------------------------------------------------
  // startSearchByImage - error handling
  // ---------------------------------------------------------------------------

  it('handles fetch failure gracefully', async () => {
    jest.spyOn(console, 'error').mockImplementation();
    jest.spyOn(console, 'warn').mockImplementation();
    global.fetch = jest.fn().mockRejectedValue(new Error('Network error'));

    const { result } = renderHook(() => useSearchByImage({ vstApiUrl: MOCK_VST_URL, mdxWebApiUrl: MOCK_MDX_URL }));

    await act(async () => {
      await result.current.startSearchByImage('sensor-1', 'Camera-1', '2025-01-01T00:00:00.000Z', 10, 'http://example.com/video.mp4');
    });

    expect(result.current.searchByImageActive).toBe(true);
    expect(result.current.searchByImageLoading).toBe(false);
    expect(result.current.searchByImageError).toBe('Network error');
  });

  it('handles non-ok picture API response', async () => {
    jest.spyOn(console, 'error').mockImplementation();
    jest.spyOn(console, 'warn').mockImplementation();
    global.fetch = jest.fn().mockResolvedValue(mockFetchResponse(null, false, 500));

    const { result } = renderHook(() => useSearchByImage({ vstApiUrl: MOCK_VST_URL, mdxWebApiUrl: MOCK_MDX_URL }));

    await act(async () => {
      await result.current.startSearchByImage('sensor-1', 'Camera-1', '2025-01-01T00:00:00.000Z', 10, 'http://example.com/video.mp4');
    });

    expect(result.current.searchByImageError).toContain('Failed to fetch frame picture: 500');
  });

  // ---------------------------------------------------------------------------
  // Frame metadata - bbox parsing
  // ---------------------------------------------------------------------------

  it('parses bbox with alternative field names (left/top/right/bottom)', async () => {
    const objects = [{
      objectId: 'alt-obj-1',
      bbox: { left: 5, top: 10, right: 50, bottom: 100 },
    }];

    global.fetch = jest.fn()
      .mockResolvedValueOnce(mockFetchResponse(null))
      .mockResolvedValueOnce(mockFetchResponse(mockFrameApiResponse(objects)));

    const { result } = renderHook(() => useSearchByImage({ vstApiUrl: MOCK_VST_URL, mdxWebApiUrl: MOCK_MDX_URL }));

    await act(async () => {
      await result.current.startSearchByImage('sensor-1', 'Camera-1', '2025-01-01T00:00:00.000Z', 10, 'http://example.com/video.mp4');
    });

    expect(result.current.searchByImageFrameData?.objects[0]?.bbox).toEqual({
      leftX: 5, topY: 10, rightX: 50, bottomY: 100,
    });
  });

  it('filters out objects without id or objectId', async () => {
    const objects = [
      { id: 'valid-1', bbox: { leftX: 0, topY: 0, rightX: 10, bottomY: 10 } },
      { bbox: { leftX: 0, topY: 0, rightX: 10, bottomY: 10 } },
      { objectId: 'valid-2', bbox: { leftX: 0, topY: 0, rightX: 10, bottomY: 10 } },
    ];

    global.fetch = jest.fn()
      .mockResolvedValueOnce(mockFetchResponse(null))
      .mockResolvedValueOnce(mockFetchResponse(mockFrameApiResponse(objects)));

    const { result } = renderHook(() => useSearchByImage({ vstApiUrl: MOCK_VST_URL, mdxWebApiUrl: MOCK_MDX_URL }));

    await act(async () => {
      await result.current.startSearchByImage('sensor-1', 'Camera-1', '2025-01-01T00:00:00.000Z', 10, 'http://example.com/video.mp4');
    });

    expect(result.current.searchByImageFrameData?.objects).toHaveLength(2);
    expect(result.current.searchByImageFrameData?.objects.map((o) => o.id)).toEqual(['valid-1', 'valid-2']);
  });

  it('selects closest frame when multiple frames returned', async () => {
    const frames = [
      { timestamp: '2025-01-01T00:00:08.000Z', objects: [{ id: 'far', bbox: {} }] },
      { timestamp: '2025-01-01T00:00:09.950Z', objects: [{ id: 'close', bbox: {} }] },
    ];

    global.fetch = jest.fn()
      .mockResolvedValueOnce(mockFetchResponse(null))
      .mockResolvedValueOnce(mockFetchResponse(frames));

    const { result } = renderHook(() => useSearchByImage({ vstApiUrl: MOCK_VST_URL, mdxWebApiUrl: MOCK_MDX_URL }));

    await act(async () => {
      await result.current.startSearchByImage('sensor-1', 'Camera-1', '2025-01-01T00:00:00.000Z', 10, 'http://example.com/video.mp4');
    });

    expect(result.current.searchByImageFrameData?.objects[0]?.id).toBe('close');
    expect(result.current.searchByImageFrameData?.timestamp).toBe('2025-01-01T00:00:09.950Z');
  });

  it('handles frames API returning non-ok gracefully (empty bbox)', async () => {
    jest.spyOn(console, 'warn').mockImplementation();

    global.fetch = jest.fn()
      .mockResolvedValueOnce(mockFetchResponse(null))
      .mockResolvedValueOnce(mockFetchResponse(null, false, 404));

    const { result } = renderHook(() => useSearchByImage({ vstApiUrl: MOCK_VST_URL, mdxWebApiUrl: MOCK_MDX_URL }));

    await act(async () => {
      await result.current.startSearchByImage('sensor-1', 'Camera-1', '2025-01-01T00:00:00.000Z', 10, 'http://example.com/video.mp4');
    });

    expect(result.current.searchByImageActive).toBe(true);
    expect(result.current.searchByImageError).toBeNull();
    expect(result.current.searchByImageFrameData?.objects).toEqual([]);
  });

  // ---------------------------------------------------------------------------
  // cancelSearchByImage
  // ---------------------------------------------------------------------------

  it('resets all state on cancelSearchByImage', async () => {
    global.fetch = jest.fn()
      .mockResolvedValueOnce(mockFetchResponse(null))
      .mockResolvedValueOnce(mockFetchResponse(mockFrameApiResponse([makeBboxObject('obj-1')])));

    const { result } = renderHook(() => useSearchByImage({ vstApiUrl: MOCK_VST_URL, mdxWebApiUrl: MOCK_MDX_URL }));

    await act(async () => {
      await result.current.startSearchByImage('sensor-1', 'Camera-1', '2025-01-01T00:00:00.000Z', 10, 'http://example.com/video.mp4');
    });
    expect(result.current.searchByImageActive).toBe(true);

    act(() => result.current.cancelSearchByImage());

    expect(result.current.searchByImageActive).toBe(false);
    expect(result.current.searchByImageLoading).toBe(false);
    expect(result.current.searchByImageError).toBeNull();
    expect(result.current.searchByImageFrameData).toBeNull();
  });
});

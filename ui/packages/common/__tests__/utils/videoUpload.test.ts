// SPDX-License-Identifier: MIT
import { getUploadUrl, uploadFile } from '../../lib-src/utils/videoUpload';
import { createMockFile, mockFetchResponse } from '../../test-helpers';

describe('getUploadUrl', () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('returns url from API response', async () => {
    global.fetch = mockFetchResponse({ url: 'https://presigned.example.com/upload' });

    const result = await getUploadUrl('video.mp4', 'http://api.test');

    expect(result).toBe('https://presigned.example.com/upload');
    expect(global.fetch).toHaveBeenCalledWith(
      'http://api.test/videos',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: 'video.mp4' }),
      })
    );
  });

  it('includes formData in request body', async () => {
    global.fetch = mockFetchResponse({ url: 'https://presigned.example.com/upload' });

    await getUploadUrl('video.mp4', 'http://api.test', {
      sensorId: 'sensor-1',
      streamId: 'stream-1',
    });

    expect(global.fetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        body: JSON.stringify({
          filename: 'video.mp4',
          sensorId: 'sensor-1',
          streamId: 'stream-1',
        }),
      })
    );
  });

  it('passes AbortSignal to fetch', async () => {
    const controller = new AbortController();
    global.fetch = mockFetchResponse({ url: 'https://presigned.example.com/upload' });

    await getUploadUrl('video.mp4', 'http://api.test', undefined, controller.signal);

    expect((global.fetch as jest.Mock).mock.calls[0][1].signal).toBe(controller.signal);
  });

  it('throws with statusText when response not ok', async () => {
    global.fetch = mockFetchResponse({}, false, 400);

    await expect(getUploadUrl('video.mp4', 'http://api.test')).rejects.toThrow();
  });

  it('throws with detail message when error has detail', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 400,
      statusText: 'Bad Request',
      json: () => Promise.resolve({ detail: 'Invalid filename' }),
    });

    await expect(getUploadUrl('video.mp4', 'http://api.test')).rejects.toThrow(
      'Invalid filename'
    );
  });
});

describe('uploadFile', () => {
  let originalFetch: typeof global.fetch;
  let originalXHR: typeof XMLHttpRequest;

  beforeEach(() => {
    originalFetch = global.fetch;
    originalXHR = global.XMLHttpRequest;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    global.XMLHttpRequest = originalXHR;
  });

  it('returns FileUploadResult after successful upload', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({ url: 'https://presigned.example.com/upload' }),
    });

    const mockResult = {
      filename: 'test.mp4',
      bytes: 1024,
      sensorId: 'sensor-1',
      streamId: 'stream-1',
      filePath: '/path/to/file',
      timestamp: '2024-01-15T10:00:00Z',
    };

    const createMockXHR = () => {
      const handlers: Record<string, () => void> = {};
      return {
        open: jest.fn(),
        setRequestHeader: jest.fn(),
        send: jest.fn(function (this: any) {
          setTimeout(() => {
            Object.defineProperty(this, 'status', { value: 200 });
            Object.defineProperty(this, 'responseText', {
              value: JSON.stringify(mockResult),
            });
            handlers['load']?.();
          }, 0);
        }),
        upload: { addEventListener: jest.fn() },
        addEventListener: jest.fn((ev: string, fn: () => void) => {
          handlers[ev] = fn;
        }),
        abort: jest.fn(),
      };
    };

    global.XMLHttpRequest = jest.fn().mockImplementation(createMockXHR) as any;

    const file = createMockFile();
    const result = await uploadFile(
      file,
      'http://api.test',
      { sensorId: 'sensor-1' }
    );

    expect(result).toEqual(mockResult);
  });

  it('throws when abortSignal is already aborted', async () => {
    const controller = new AbortController();
    controller.abort();

    const file = createMockFile();

    await expect(
      uploadFile(file, 'http://api.test', {}, undefined, controller.signal)
    ).rejects.toThrow('Upload was cancelled');
  });

  it('uses requestFilename when provided', async () => {
    const handlers: Record<string, () => void> = {};
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({ url: 'https://presigned.example.com/upload' }),
    });

    global.XMLHttpRequest = jest.fn().mockImplementation(() => ({
      open: jest.fn(),
      setRequestHeader: jest.fn(),
      send: jest.fn(function (this: any) {
        setTimeout(() => {
          Object.defineProperty(this, 'status', { value: 200 });
          Object.defineProperty(this, 'responseText', {
            value: JSON.stringify({
              filename: 'custom-name',
              bytes: 0,
              sensorId: '',
              streamId: '',
              filePath: '',
              timestamp: '',
            }),
          });
          handlers['load']?.();
        }, 0);
      }),
      upload: { addEventListener: jest.fn() },
      addEventListener: jest.fn((ev: string, fn: () => void) => {
        handlers[ev] = fn;
      }),
      abort: jest.fn(),
    })) as any;

    const file = createMockFile('original.mp4');
    await uploadFile(
      file,
      'http://api.test',
      {},
      undefined,
      undefined,
      'custom-name.mp4'
    );

    expect(JSON.parse((global.fetch as jest.Mock).mock.calls[0][1].body)).toMatchObject({
      filename: 'custom-name.mp4',
    });
  });
});

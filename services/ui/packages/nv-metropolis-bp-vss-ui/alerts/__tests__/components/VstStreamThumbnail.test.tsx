// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import {
  VstStreamThumbnail,
  clearSensorListCache,
  clearVstStreamThumbnailCache,
} from '../../lib-src/components/VstStreamThumbnail';
import * as vstSensorList from '../../lib-src/utils/vstSensorList';

const segments = [{ startTime: '2025-01-01T00:00:00.000Z', endTime: '2025-01-01T00:00:25.000Z' }];

const streamResponse = (url: string) => {
  const match = /\/sensor\/([^/]+)\/streams$/.exec(url);
  return match ? [{ streamId: decodeURIComponent(match[1]) }] : undefined;
};

const mockVstFetch = (sensors: unknown, timelines: unknown = segments) =>
  jest.fn().mockImplementation((url: string) =>
    jsonResponse(url.endsWith('/sensor/list') ? sensors : streamResponse(url) ?? timelines),
  );

const jsonResponse = (body: unknown) =>
  Promise.resolve({
    ok: true,
    json: () => Promise.resolve(body),
  } as Response);

describe('VstStreamThumbnail picture URL', () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
    clearSensorListCache();
    clearVstStreamThumbnailCache();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    clearSensorListCache();
    clearVstStreamThumbnailCache();
  });

  it('uses the uploaded recording timeline even when the wall clock is a year later', async () => {
    // Pin Date.now so the computed startTime is deterministic.
    const fixedNow = Date.UTC(2026, 0, 15, 12, 0, 0); // 2026-01-15T12:00:00.000Z
    jest.spyOn(Date, 'now').mockReturnValue(fixedNow);

    global.fetch = mockVstFetch([{ name: 'sample.mp4', sensorId: 'id-1', state: 'online' }]);

    render(
      <VstStreamThumbnail
        vstApiUrl="http://vst.test"
        sensorName="sample.mp4"
        isDark={false}
      />,
    );

    const img = await screen.findByTestId('vst-stream-thumbnail');
    const src = img.getAttribute('src') ?? '';
    const url = new URL(src);

    // Endpoint change introduced by this PR: replay (not live).
    expect(url.pathname).toBe('/v1/replay/stream/id-1/picture');

    // The upload timeline, not the wall clock, determines the frame.
    expect(url.searchParams.get('startTime')).toBe('2025-01-01T00:00:20.000Z');

    // Raw query string is percent-encoded (colons must be %3A).
    expect(url.search).toBe('?startTime=2025-01-01T00%3A00%3A20.000Z');
  });

  it('percent-encodes the sensorId path segment', async () => {
    global.fetch = mockVstFetch([{ name: 'cam', sensorId: 'id with space/slash', state: 'online' }]);

    render(
      <VstStreamThumbnail
        vstApiUrl="http://vst.test"
        sensorName="cam"
        isDark={false}
      />,
    );

    const img = await screen.findByTestId('vst-stream-thumbnail');
    expect(img.getAttribute('src')).toContain(
      '/v1/replay/stream/id%20with%20space%2Fslash/picture',
    );
  });

  it('strips trailing slashes from vstApiUrl before assembling the URL', async () => {
    global.fetch = mockVstFetch([{ name: 'cam', sensorId: 'id-1', state: 'online' }]);

    render(
      <VstStreamThumbnail
        vstApiUrl="http://vst.test///"
        sensorName="cam"
        isDark={false}
      />,
    );

    const img = await screen.findByTestId('vst-stream-thumbnail');
    const src = img.getAttribute('src') ?? '';
    expect(src.startsWith('http://vst.test/v1/replay/stream/id-1/picture?')).toBe(true);
    expect(src).not.toContain('vst.test//v1');
  });

  it('selects the latest valid segment even if timelines are out of order', async () => {
    global.fetch = mockVstFetch([{ name: 'cam', sensorId: 'id-1', state: 'online' }], [
      { startTime: '2025-01-02T00:00:00Z', endTime: '2025-01-02T00:00:30Z' },
      ...segments,
      { startTime: 'invalid', endTime: '2027-01-01T00:00:00Z' },
    ]);
    render(<VstStreamThumbnail vstApiUrl="http://vst.test" sensorName="cam" isDark={false} />);
    const img = await screen.findByTestId('vst-stream-thumbnail');
    expect(new URL(img.getAttribute('src')!).searchParams.get('startTime')).toBe('2025-01-02T00:00:25.000Z');
  });

  it('keeps a short recording preview inside its timeline', async () => {
    global.fetch = mockVstFetch([{ name: 'cam', sensorId: 'id-1', state: 'online' }], [
      { startTime: '2025-01-01T00:00:00Z', endTime: '2025-01-01T00:00:02Z' },
    ]);
    render(<VstStreamThumbnail vstApiUrl="http://vst.test" sensorName="cam" isDark={false} />);
    const img = await screen.findByTestId('vst-stream-thumbnail');
    expect(new URL(img.getAttribute('src')!).searchParams.get('startTime')).toBe('2025-01-01T00:00:01.000Z');
  });

  it.each([
    [],
    [{ startTime: 'invalid', endTime: 'invalid' }],
    [{ startTime: '2025-01-01T00:00:02Z', endTime: '2025-01-01T00:00:00Z' }],
  ].map((timeline) => ({ timeline })))('shows a placeholder when there is no valid recording: %j', async ({ timeline }) => {
    global.fetch = mockVstFetch([{ name: 'cam', sensorId: 'id-1', state: 'online' }], timeline);
    render(<VstStreamThumbnail vstApiUrl="http://vst.test" sensorName="cam" isDark={false} />);
    await screen.findByText('No thumbnail');
    expect(screen.queryByTestId('vst-stream-thumbnail')).not.toBeInTheDocument();
  });

  it('shows a placeholder when the timeline service fails', async () => {
    global.fetch = jest.fn().mockImplementation((url: string) =>
      url.endsWith('/sensor/list')
        ? jsonResponse([{ name: 'cam', sensorId: 'id-1', state: 'online' }])
        : streamResponse(url) ? jsonResponse(streamResponse(url)) : Promise.resolve({ ok: false, status: 503 } as Response),
    );
    render(<VstStreamThumbnail vstApiUrl="http://vst.test" sensorName="cam" isDark={false} />);
    await screen.findByText('No thumbnail');
    expect(screen.queryByTestId('vst-stream-thumbnail')).not.toBeInTheDocument();
  });


  it('uses the latest recording stream when a sensor has multiple uploaded videos', async () => {
    global.fetch = jest.fn().mockImplementation((url: string) => {
      if (url.endsWith('/sensor/list')) return jsonResponse([{ name: 'cam', sensorId: 'sensor-1', state: 'online' }]);
      if (url.endsWith('/streams')) return jsonResponse([{ streamId: 'old-stream' }, { streamId: 'new-stream' }]);
      return jsonResponse(url.includes('/new-stream/')
        ? [{ startTime: '2025-02-01T00:00:00Z', endTime: '2025-02-01T00:00:30Z' }]
        : segments);
    });
    render(<VstStreamThumbnail vstApiUrl="http://vst.test" sensorName="cam" isDark={false} />);
    const img = await screen.findByTestId('vst-stream-thumbnail');
    const url = new URL(img.getAttribute('src')!);
    expect(url.pathname).toBe('/v1/replay/stream/new-stream/picture');
    expect(url.searchParams.get('startTime')).toBe('2025-02-01T00:00:25.000Z');
    expect(global.fetch).not.toHaveBeenCalledWith('http://vst.test/v1/storage/sensor-1/timelines');
  });

  it('uses another recorded stream if one stream timeline request fails', async () => {
    global.fetch = jest.fn().mockImplementation((url: string) => {
      if (url.endsWith('/sensor/list')) return jsonResponse([{ name: 'cam', sensorId: 'sensor-1', state: 'online' }]);
      if (url.endsWith('/streams')) return jsonResponse([{ streamId: 'failed-stream' }, { streamId: 'good-stream' }]);
      return url.includes('/failed-stream/') ? Promise.resolve({ ok: false, status: 503 } as Response) : jsonResponse(segments);
    });
    render(<VstStreamThumbnail vstApiUrl="http://vst.test" sensorName="cam" isDark={false} />);
    const img = await screen.findByTestId('vst-stream-thumbnail');
    expect(img.getAttribute('src')).toContain('/good-stream/picture');
  });

  it.each(['empty', 'failed'])('recovers from a %s timeline without reopening the editor', async (failure) => {
    let recovered = false;
    global.fetch = jest.fn().mockImplementation((url: string) => {
      if (url.endsWith('/sensor/list')) return jsonResponse([{ name: 'cam', sensorId: 'id-1', state: 'online' }]);
      const streams = streamResponse(url);
      if (streams) return jsonResponse(streams);
      if (recovered) return jsonResponse(segments);
      return failure === 'empty' ? jsonResponse([]) : Promise.resolve({ ok: false, status: 503 } as Response);
    });
    render(<VstStreamThumbnail vstApiUrl="http://vst.test" sensorName="cam" isDark={false} />);
    await screen.findByText('No thumbnail');
    recovered = true;
    fireEvent.click(screen.getByRole('button', { name: 'Retry thumbnail' }));
    const img = await screen.findByTestId('vst-stream-thumbnail');
    expect(img.getAttribute('src')).toContain('/id-1/picture');
    expect(jest.mocked(global.fetch).mock.calls.filter(([url]) => String(url).endsWith('/sensor/list'))).toHaveLength(2);
  });

  it('allows retrying a picture that failed to load', async () => {
    global.fetch = mockVstFetch([{ name: 'cam', sensorId: 'id-1', state: 'online' }]);
    render(<VstStreamThumbnail vstApiUrl="http://vst.test" sensorName="cam" isDark={false} />);
    fireEvent.error(await screen.findByTestId('vst-stream-thumbnail'));
    expect(screen.getByText('Frame unavailable')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry thumbnail' }));
    const img = await screen.findByTestId('vst-stream-thumbnail');
    fireEvent.load(img);
    expect(screen.queryByText('Frame unavailable')).not.toBeInTheDocument();
  });


  it('times out a stalled timeline and still shows a recorded frame from another stream', async () => {
    jest.useFakeTimers();
    try {
      global.fetch = jest.fn().mockImplementation((url: string, options?: RequestInit) => {
        if (url.endsWith('/sensor/list')) return jsonResponse([{ name: 'cam', sensorId: 'sensor-1', state: 'online' }]);
        if (url.endsWith('/streams')) return jsonResponse([{ streamId: 'stalled-stream' }, { streamId: 'good-stream' }]);
        if (url.includes('/stalled-stream/')) {
          return new Promise<Response>((_resolve, reject) => {
            options!.signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
          });
        }
        return jsonResponse(segments);
      });
      render(<VstStreamThumbnail vstApiUrl="http://vst.test" sensorName="cam" isDark={false} />);
      await act(async () => {});
      expect(screen.getByText('Loading thumbnail…')).toBeInTheDocument();
      await act(async () => { await jest.advanceTimersByTimeAsync(5000); });
      expect(screen.getByTestId('vst-stream-thumbnail').getAttribute('src')).toContain('/good-stream/picture');
    } finally {
      jest.useRealTimers();
    }
  });

  it('refreshes the sensor catalog only for the retry, not later sensor changes', async () => {
    const sensorMap = new Map([['cam-a', 'id-a'], ['cam-b', 'id-b']]);
    const sensorFetch = jest.spyOn(vstSensorList, 'fetchSensorMap').mockResolvedValue(sensorMap);
    global.fetch = mockVstFetch([]);
    const { rerender } = render(<VstStreamThumbnail vstApiUrl="http://vst.test" sensorName="cam-a" isDark={false} />);
    fireEvent.error(await screen.findByTestId('vst-stream-thumbnail'));
    fireEvent.click(screen.getByRole('button', { name: 'Retry thumbnail' }));
    await screen.findByTestId('vst-stream-thumbnail');
    expect(sensorFetch).toHaveBeenLastCalledWith('http://vst.test', { forceRefresh: true });
    rerender(<VstStreamThumbnail vstApiUrl="http://vst.test" sensorName="cam-b" isDark={false} />);
    await waitFor(() => expect(screen.getByTestId('vst-stream-thumbnail').getAttribute('src')).toContain('/id-b/picture'));
    expect(sensorFetch).toHaveBeenLastCalledWith('http://vst.test', { forceRefresh: false });
  });

});

describe('VstStreamThumbnail remount cache', () => {
  const vstApiUrl = 'http://vst.example/';
  const sensorName = 'warehouse-cam';

  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = mockVstFetch([]);
    clearVstStreamThumbnailCache();
    jest
      .spyOn(vstSensorList, 'fetchSensorMap')
      .mockResolvedValue(new Map([[sensorName, 'sensor-42']]));
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('shows cached thumbnail immediately on remount without a loading placeholder', async () => {
    const { unmount } = render(
      <VstStreamThumbnail isDark={false} vstApiUrl={vstApiUrl} sensorName={sensorName} />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('vst-stream-thumbnail')).toBeInTheDocument();
    });

    unmount();
    jest.mocked(vstSensorList.fetchSensorMap).mockClear();

    await act(async () => {
      render(
        <VstStreamThumbnail isDark={false} vstApiUrl={vstApiUrl} sensorName={sensorName} />,
      );
    });

    expect(screen.getByTestId('vst-stream-thumbnail')).toBeInTheDocument();
    expect(screen.queryByText('Loading thumbnail…')).not.toBeInTheDocument();
    expect(vstSensorList.fetchSensorMap).toHaveBeenCalled();
  });
});

describe('VstStreamThumbnail broken frame recovery', () => {
  const vstApiUrl = 'http://vst.example';
  const sensorA = 'cam-a';
  const sensorB = 'cam-b';

  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = mockVstFetch([]);
    clearVstStreamThumbnailCache();
    jest.spyOn(vstSensorList, 'fetchSensorMap').mockImplementation(async (url) => {
      if (url !== vstApiUrl) {
        return new Map();
      }
      return new Map([
        [sensorA, 'id-a'],
        [sensorB, 'id-b'],
      ]);
    });
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('shows a new sensor thumbnail after the prior sensor image failed', async () => {
    const { rerender } = render(
      <VstStreamThumbnail isDark={false} vstApiUrl={vstApiUrl} sensorName={sensorA} />,
    );

    const imgA = await screen.findByTestId('vst-stream-thumbnail');
    fireEvent.error(imgA);
    expect(screen.getByText('Frame unavailable')).toBeInTheDocument();

    rerender(
      <VstStreamThumbnail isDark={false} vstApiUrl={vstApiUrl} sensorName={sensorB} />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('vst-stream-thumbnail')).toBeInTheDocument();
    });
    expect(screen.queryByText('Frame unavailable')).not.toBeInTheDocument();
    expect(screen.getByTestId('vst-stream-thumbnail').getAttribute('src')).toContain(
      '/v1/replay/stream/id-b/picture',
    );
  });

  it('ignores the previous sensor timeline when it resolves after switching sensors', async () => {
    let resolvePrevious!: (value: Response) => void;
    const previousTimeline = new Promise<Response>((resolve) => {
      resolvePrevious = resolve;
    });
    global.fetch = jest.fn().mockImplementation((url: string) =>
      url.includes('/storage/id-a/') ? previousTimeline : jsonResponse(streamResponse(url) ?? segments),
    );
    const { rerender } = render(
      <VstStreamThumbnail isDark={false} vstApiUrl={vstApiUrl} sensorName={sensorA} />,
    );
    await waitFor(() => expect(global.fetch).toHaveBeenCalledWith(
      `${vstApiUrl}/v1/storage/id-a/timelines`, expect.objectContaining({ signal: expect.anything() }),
    ));
    rerender(
      <VstStreamThumbnail isDark={false} vstApiUrl={vstApiUrl} sensorName={sensorB} />,
    );
    await screen.findByTestId('vst-stream-thumbnail');
    await act(async () => {
      resolvePrevious(await jsonResponse(segments));
    });
    expect(screen.getByTestId('vst-stream-thumbnail').getAttribute('src')).toContain(
      '/v1/replay/stream/id-b/picture',
    );
  });
});

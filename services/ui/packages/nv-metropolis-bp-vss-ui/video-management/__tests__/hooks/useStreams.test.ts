// SPDX-License-Identifier: MIT
import { act, renderHook, waitFor } from '@testing-library/react';
import { useStreams } from '../../lib-src/hooks/useStreams';
import { DELETED_STREAM_POLL_DELAYS_MS } from '../../lib-src/constants';
import type { StreamInfo } from '../../lib-src/types';
import { videoStream, rtspStream } from '../helpers/streamFixtures';

const VST_API_URL = 'https://vst.example.com/vst/api';

function streamsPayload(streams: StreamInfo[]) {
  return streams.map((stream) => ({ [stream.sensorId]: [stream] }));
}

function mockVstStreams(...responses: StreamInfo[][]) {
  const queue = [...responses];
  const fetchMock = jest.fn(async () => {
    const streams = queue.length > 1 ? queue.shift()! : queue[0];
    return {
      ok: true,
      json: async () => streamsPayload(streams),
    } as Response;
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

function names(streams: StreamInfo[]) {
  return streams.map((s) => s.name);
}

describe('useStreams — waitUntilStreamsRemoved', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  it('resolves immediately when VST already dropped the sensors', async () => {
    mockVstStreams([videoStream, rtspStream], [videoStream]);

    const { result } = renderHook(() => useStreams({ vstApiUrl: VST_API_URL }));
    await waitFor(() => expect(result.current.streams).toHaveLength(2));

    let waitResult: { remainingSensorIds: string[] } | undefined;
    await act(async () => {
      waitResult = await result.current.waitUntilStreamsRemoved([rtspStream.sensorId]);
    });

    expect(waitResult).toEqual({ remainingSensorIds: [] });
    expect(names(result.current.streams)).toEqual([videoStream.name]);
  });

  it('polls until VST drops the sensor, then resolves with none remaining', async () => {
    // Initial load still has both; first wait-poll still has RTSP; second drops it
    const fetchMock = mockVstStreams(
      [videoStream, rtspStream],
      [videoStream, rtspStream],
      [videoStream],
    );

    const { result } = renderHook(() => useStreams({ vstApiUrl: VST_API_URL }));
    await waitFor(() => expect(result.current.streams).toHaveLength(2));

    let waitResult: { remainingSensorIds: string[] } | undefined;
    await act(async () => {
      const pending = result.current.waitUntilStreamsRemoved([rtspStream.sensorId]);
      // Immediate silent fetch still sees RTSP; advance past first poll delay
      await jest.advanceTimersByTimeAsync(DELETED_STREAM_POLL_DELAYS_MS[0]);
      waitResult = await pending;
    });

    expect(waitResult).toEqual({ remainingSensorIds: [] });
    expect(names(result.current.streams)).toEqual([videoStream.name]);
    // Initial + immediate wait check + one delayed poll
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('returns remaining sensor ids when the poll budget is exhausted', async () => {
    mockVstStreams([videoStream, rtspStream]);

    const { result } = renderHook(() => useStreams({ vstApiUrl: VST_API_URL }));
    await waitFor(() => expect(result.current.streams).toHaveLength(2));

    let waitResult: { remainingSensorIds: string[] } | undefined;
    await act(async () => {
      const pending = result.current.waitUntilStreamsRemoved([rtspStream.sensorId]);
      await jest.advanceTimersByTimeAsync(
        DELETED_STREAM_POLL_DELAYS_MS.reduce((sum, d) => sum + d, 0),
      );
      waitResult = await pending;
    });

    expect(waitResult).toEqual({ remainingSensorIds: [rtspStream.sensorId] });
    expect(names(result.current.streams)).toEqual([videoStream.name, rtspStream.name]);
  });

  it('keeps poll fetches out of the loading state', async () => {
    mockVstStreams([videoStream, rtspStream], [videoStream]);

    const { result } = renderHook(() => useStreams({ vstApiUrl: VST_API_URL }));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.waitUntilStreamsRemoved([rtspStream.sensorId]);
    });

    expect(result.current.isLoading).toBe(false);
  });

  // A poll that errors out returns no streams, which must not be read as
  // "VST dropped the sensor" — that would close the dialog on a stale stream.
  it('does not treat a failed poll as confirmed removal', async () => {
    const fetchMock = jest.fn(async (): Promise<Response> => {
      throw new Error('network down');
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const { result } = renderHook(() => useStreams({ vstApiUrl: VST_API_URL }));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let waitResult: { remainingSensorIds: string[] } | undefined;
    await act(async () => {
      const pending = result.current.waitUntilStreamsRemoved([rtspStream.sensorId]);
      await jest.advanceTimersByTimeAsync(
        DELETED_STREAM_POLL_DELAYS_MS.reduce((sum, d) => sum + d, 0),
      );
      waitResult = await pending;
    });

    expect(waitResult).toEqual({ remainingSensorIds: [rtspStream.sensorId] });
  });

  it('keeps polling a sensor when one poll fails and a later one confirms removal', async () => {
    let call = 0;
    const responses: (StreamInfo[] | 'error')[] = [
      [videoStream, rtspStream], // initial load
      'error',                   // immediate silent check fails
      [videoStream],             // next poll confirms removal
    ];
    globalThis.fetch = jest.fn(async () => {
      const next = responses[Math.min(call, responses.length - 1)];
      call += 1;
      if (next === 'error') throw new Error('transient failure');
      return { ok: true, json: async () => streamsPayload(next) } as Response;
    }) as unknown as typeof fetch;

    const { result } = renderHook(() => useStreams({ vstApiUrl: VST_API_URL }));
    await waitFor(() => expect(result.current.streams).toHaveLength(2));

    let waitResult: { remainingSensorIds: string[] } | undefined;
    await act(async () => {
      const pending = result.current.waitUntilStreamsRemoved([rtspStream.sensorId]);
      await jest.advanceTimersByTimeAsync(DELETED_STREAM_POLL_DELAYS_MS[0]);
      waitResult = await pending;
    });

    expect(waitResult).toEqual({ remainingSensorIds: [] });
  });

  it('leaves the grid untouched when a background poll fails', async () => {
    let call = 0;
    globalThis.fetch = jest.fn(async () => {
      call += 1;
      if (call === 1) {
        return { ok: true, json: async () => streamsPayload([videoStream, rtspStream]) } as Response;
      }
      throw new Error('transient failure');
    }) as unknown as typeof fetch;

    const { result } = renderHook(() => useStreams({ vstApiUrl: VST_API_URL }));
    await waitFor(() => expect(result.current.streams).toHaveLength(2));

    await act(async () => {
      const pending = result.current.waitUntilStreamsRemoved([rtspStream.sensorId]);
      await jest.advanceTimersByTimeAsync(
        DELETED_STREAM_POLL_DELAYS_MS.reduce((sum, d) => sum + d, 0),
      );
      await pending;
    });

    expect(names(result.current.streams)).toEqual([videoStream.name, rtspStream.name]);
    expect(result.current.error).toBeNull();
  });

  it('does nothing when no sensors were deleted', async () => {
    const fetchMock = mockVstStreams([videoStream, rtspStream]);

    const { result } = renderHook(() => useStreams({ vstApiUrl: VST_API_URL }));
    await waitFor(() => expect(result.current.streams).toHaveLength(2));

    let waitResult: { remainingSensorIds: string[] } | undefined;
    await act(async () => {
      waitResult = await result.current.waitUntilStreamsRemoved([]);
      await jest.advanceTimersByTimeAsync(20_000);
    });

    expect(waitResult).toEqual({ remainingSensorIds: [] });
    expect(result.current.streams).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

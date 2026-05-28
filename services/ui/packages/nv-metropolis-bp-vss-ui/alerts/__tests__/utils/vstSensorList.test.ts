// SPDX-License-Identifier: MIT
import {
  clearSensorListCache,
  deriveSensorNameFromLiveStreamUrl,
  fetchVstLiveStreamCatalog,
  resolveSensorByName,
} from '../../lib-src/utils/vstSensorList';

const textResponse = (body: unknown) =>
  Promise.resolve({
    ok: true,
    text: () => Promise.resolve(JSON.stringify(body)),
  } as Response);

describe('vstSensorList', () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
    clearSensorListCache();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    clearSensorListCache();
  });

  it('derives sensor name from the last RTSP path segment', () => {
    expect(
      deriveSensorNameFromLiveStreamUrl(
        'rtsp://host/streamer_videos/sample.mp4?token=abc',
      ),
    ).toBe('sample.mp4');
    expect(
      deriveSensorNameFromLiveStreamUrl(
        'rtsp://host/streamer_videos/sample.mp4#fragment',
      ),
    ).toBe('sample.mp4');
  });

  it('flattens the nested VST /v1/live/streams payload', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      textResponse([
        {
          'stream-key-1': [
            {
              name: 'warehouse-cam-1',
              url: 'rtsp://10.24.142.82:30554/live/8c7338ec-2266-4eea-aeb4-c568d8944b05',
              streamId: '8c7338ec-2266-4eea-aeb4-c568d8944b05',
            },
          ],
        },
        {
          'stream-key-2': [
            {
              name: 'sample.mp4',
              url: 'rtsp://10.24.142.82:30554/sample.mp4',
              streamId: 'mp4-stream-id',
            },
          ],
        },
      ]),
    );

    const catalog = await fetchVstLiveStreamCatalog('http://vst.test');
    expect(global.fetch).toHaveBeenCalledWith('http://vst.test/v1/live/streams');
    expect(catalog).toEqual([
      {
        name: 'warehouse-cam-1',
        url: 'rtsp://10.24.142.82:30554/live/8c7338ec-2266-4eea-aeb4-c568d8944b05',
        streamId: '8c7338ec-2266-4eea-aeb4-c568d8944b05',
      },
      {
        name: 'sample.mp4',
        url: 'rtsp://10.24.142.82:30554/sample.mp4',
        streamId: 'mp4-stream-id',
      },
    ]);
  });

  it('does not cache the live-stream catalog — back-to-back calls each hit VST', async () => {
    global.fetch = jest.fn().mockResolvedValue(textResponse([]));

    await fetchVstLiveStreamCatalog('http://vst.test');
    await fetchVstLiveStreamCatalog('http://vst.test');

    expect(global.fetch).toHaveBeenCalledTimes(2);
  });

  it('resolves sensor_name and live_stream_url by sensor name', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      textResponse([
        {
          'stream-key-1': [
            {
              name: 'warehouse-cam-1',
              url: 'rtsp://10.24.142.82:30554/live/8c7338ec-2266-4eea-aeb4-c568d8944b05',
              streamId: '8c7338ec-2266-4eea-aeb4-c568d8944b05',
            },
          ],
        },
      ]),
    );

    await expect(
      resolveSensorByName('http://vst.test', 'warehouse-cam-1'),
    ).resolves.toEqual({
      sensor_name: 'warehouse-cam-1',
      live_stream_url:
        'rtsp://10.24.142.82:30554/live/8c7338ec-2266-4eea-aeb4-c568d8944b05',
    });
  });

  it('returns undefined when the sensor is not in the VST live-stream catalog', async () => {
    global.fetch = jest.fn().mockResolvedValue(textResponse([]));

    await expect(
      resolveSensorByName('http://vst.test', 'unknown-sensor'),
    ).resolves.toBeUndefined();
  });
});

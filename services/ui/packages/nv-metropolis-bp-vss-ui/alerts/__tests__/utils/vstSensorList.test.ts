// SPDX-License-Identifier: MIT
import {
  clearSensorListCache,
  deriveSensorNameFromLiveStreamUrl,
  resolveSensorForLiveStreamUrl,
} from '../../lib-src/utils/vstSensorList';

const jsonResponse = (body: unknown) =>
  Promise.resolve({
    ok: true,
    json: () => Promise.resolve(body),
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

  it('resolves sensor_id from VST sensor list for an online sensor', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      jsonResponse([
        { name: 'sample.mp4', sensorId: 'id-1', state: 'online' },
        { name: 'offline.mp4', sensorId: 'id-2', state: 'offline' },
      ]),
    );

    await expect(
      resolveSensorForLiveStreamUrl('http://vst.test', 'rtsp://host/sample.mp4'),
    ).resolves.toEqual({ sensor_name: 'sample.mp4', sensor_id: 'id-1' });

    expect(global.fetch).toHaveBeenCalledWith('http://vst.test/v1/sensor/list');
  });

  it('rejects when the sensor is not registered online', async () => {
    global.fetch = jest.fn().mockResolvedValue(jsonResponse([]));

    await expect(
      resolveSensorForLiveStreamUrl('http://vst.test', 'rtsp://host/unknown.mp4'),
    ).rejects.toThrow(/not registered with VST/i);
  });
});

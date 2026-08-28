// SPDX-License-Identifier: MIT
import { addRtspStream, deleteRtspStream } from '../lib-src/rtspStream';

describe('direct VST RTSP lifecycle', () => {
  beforeEach(() => {
    global.fetch = jest.fn();
  });

  it('adds an RTSP sensor directly through VST', async () => {
    (global.fetch as jest.Mock).mockResolvedValue(new Response(
      JSON.stringify({ sensorId: 'sensor-1' }),
      { status: 200 },
    ));

    await expect(addRtspStream(
      'https://host/vst/api',
      { sensorUrl: 'rtsp://camera/stream', name: 'Camera 1' },
    )).resolves.toEqual({ sensorId: 'sensor-1' });

    expect(global.fetch).toHaveBeenCalledWith(
      'https://host/vst/api/v1/sensor/add',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ sensorUrl: 'rtsp://camera/stream', name: 'Camera 1' }),
      }),
    );
  });

  it('deletes an RTSP sensor by VST sensor ID', async () => {
    (global.fetch as jest.Mock).mockResolvedValue(new Response('true', { status: 200 }));

    await expect(deleteRtspStream(
      'https://host/vst/api',
      'sensor/a',
    )).resolves.toBe(true);

    expect(global.fetch).toHaveBeenCalledWith(
      'https://host/vst/api/v1/sensor/sensor%2Fa',
      expect.objectContaining({ method: 'DELETE' }),
    );
  });

  it('surfaces VIOS error messages', async () => {
    (global.fetch as jest.Mock).mockResolvedValue(new Response(
      JSON.stringify({ error_code: 'CameraNotFoundError', error_message: 'Camera is missing' }),
      { status: 400 },
    ));

    await expect(addRtspStream(
      'https://host/vst/api',
      { sensorUrl: 'rtsp://bad', name: 'Bad' },
    )).rejects.toThrow('Camera is missing');
  });
});

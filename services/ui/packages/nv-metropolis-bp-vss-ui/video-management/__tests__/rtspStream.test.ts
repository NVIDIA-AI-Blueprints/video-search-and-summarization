// SPDX-License-Identifier: MIT
import { addRtspStream, deleteRtspStream } from '../lib-src/rtspStream';

describe('agent-owned RTSP lifecycle', () => {
  beforeEach(() => {
    global.fetch = jest.fn();
  });

  it('adds an RTSP sensor through the agent', async () => {
    (global.fetch as jest.Mock).mockResolvedValue(new Response(
      JSON.stringify({ status: 'success', message: 'added' }),
      { status: 200 },
    ));

    await expect(addRtspStream(
      'https://host/api/v1',
      { sensorUrl: 'rtsp://camera/stream', name: 'Camera 1' },
    )).resolves.toEqual({ status: 'success', message: 'added' });

    expect(global.fetch).toHaveBeenCalledWith(
      'https://host/api/v1/rtsp-streams/add',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ sensorUrl: 'rtsp://camera/stream', name: 'Camera 1' }),
      }),
    );
  });

  it('deletes an RTSP sensor by name through the agent', async () => {
    (global.fetch as jest.Mock).mockResolvedValue(new Response(
      JSON.stringify({ status: 'success', message: 'deleted', name: 'sensor/a' }),
      { status: 200 },
    ));

    await expect(deleteRtspStream(
      'https://host/api/v1',
      'sensor/a',
    )).resolves.toEqual({ status: 'success', message: 'deleted', name: 'sensor/a' });

    expect(global.fetch).toHaveBeenCalledWith(
      'https://host/api/v1/rtsp-streams/delete/sensor%2Fa',
      expect.objectContaining({ method: 'DELETE' }),
    );
  });

  it('surfaces agent error messages', async () => {
    (global.fetch as jest.Mock).mockResolvedValue(new Response(
      JSON.stringify({ error_code: 'CameraNotFoundError', error_message: 'Camera is missing' }),
      { status: 400 },
    ));

    await expect(addRtspStream(
      'https://host/api/v1',
      { sensorUrl: 'rtsp://bad', name: 'Bad' },
    )).rejects.toThrow('Camera is missing');
  });
});

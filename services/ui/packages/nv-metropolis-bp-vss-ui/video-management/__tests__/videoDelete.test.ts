// SPDX-License-Identifier: MIT
import { deleteVideo } from '../lib-src/videoDelete';

describe('direct VST uploaded-video deletion', () => {
  beforeEach(() => {
    global.fetch = jest.fn();
  });

  it('deletes the full storage range before deleting the sensor', async () => {
    (global.fetch as jest.Mock)
      .mockResolvedValueOnce(new Response(JSON.stringify({ spaceSaved: 12.5 }), { status: 200 }))
      .mockResolvedValueOnce(new Response('true', { status: 200 }));

    await expect(deleteVideo(
      'https://host/vst/api',
      'sensor-1',
      '2025-01-01T00:00:00.000Z',
      '2025-01-01T01:00:00.000Z',
    )).resolves.toEqual({ sensorId: 'sensor-1', spaceSaved: 12.5 });

    expect((global.fetch as jest.Mock).mock.calls[0][0]).toBe(
      'https://host/vst/api/v1/storage/file/sensor-1?startTime=2025-01-01T00%3A00%3A00.000Z&endTime=2025-01-01T01%3A00%3A00.000Z',
    );
    expect((global.fetch as jest.Mock).mock.calls[1][0]).toBe(
      'https://host/vst/api/v1/sensor/sensor-1',
    );
  });

  it('accepts sensor 404 after storage deletion cascades cleanup', async () => {
    (global.fetch as jest.Mock)
      .mockResolvedValueOnce(new Response('{}', { status: 200 }))
      .mockResolvedValueOnce(new Response('', { status: 404 }));

    await expect(deleteVideo(
      'https://host/vst/api',
      'sensor-1',
      '2025-01-01T00:00:00Z',
      '2025-01-01T01:00:00Z',
    )).resolves.toEqual({ sensorId: 'sensor-1', spaceSaved: undefined });
  });

  it('stops before sensor deletion when storage deletion fails', async () => {
    (global.fetch as jest.Mock).mockResolvedValue(new Response(
      JSON.stringify({ error_message: 'Storage is busy' }),
      { status: 409 },
    ));

    await expect(deleteVideo(
      'https://host/vst/api',
      'sensor-1',
      '2025-01-01T00:00:00Z',
      '2025-01-01T01:00:00Z',
    )).rejects.toThrow('Storage is busy');
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });
});

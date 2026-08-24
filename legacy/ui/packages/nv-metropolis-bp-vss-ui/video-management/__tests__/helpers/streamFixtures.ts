// SPDX-License-Identifier: MIT
import type { StreamInfo } from '../../lib-src/types';

export const defaultMetadata = {
  bitrate: '',
  codec: 'H264',
  framerate: '30',
  govlength: '',
  resolution: '',
};

export function makeStream(overrides: Partial<StreamInfo> & { name: string; streamId: string }): StreamInfo {
  return {
    isMain: false,
    metadata: defaultMetadata,
    name: overrides.name,
    streamId: overrides.streamId,
    url: overrides.url ?? 'https://example.com/video.mp4',
    vodUrl: overrides.vodUrl ?? 'https://example.com/vod/video.mp4',
    sensorId: overrides.sensorId ?? 'sensor-1',
    ...overrides,
  };
}

export const videoStream = makeStream({ name: 'test_video', streamId: 'vid-1', sensorId: 'sensor-vid' });

export const rtspStream = makeStream({
  name: 'Camera 1',
  streamId: 'rtsp-1',
  sensorId: 'sensor-rtsp',
  url: 'rtsp://host/stream',
  vodUrl: 'rtsp://host/stream',
});

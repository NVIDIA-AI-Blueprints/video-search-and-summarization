// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen } from '@testing-library/react';
import {
  VstStreamThumbnail,
  clearSensorListCache,
} from '../../lib-src/components/VstStreamThumbnail';

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
  });

  afterEach(() => {
    global.fetch = originalFetch;
    clearSensorListCache();
  });

  it('builds /v1/replay/stream/{id}/picture with startTime 5s before now, URL-encoded', async () => {
    // Pin Date.now so the computed startTime is deterministic.
    const fixedNow = Date.UTC(2026, 0, 15, 12, 0, 0); // 2026-01-15T12:00:00.000Z
    jest.spyOn(Date, 'now').mockReturnValue(fixedNow);

    global.fetch = jest.fn().mockResolvedValue(
      jsonResponse([{ name: 'sample.mp4', sensorId: 'id-1', state: 'online' }]),
    );

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

    // Decoded value is exactly 5s before the pinned now.
    expect(url.searchParams.get('startTime')).toBe('2026-01-15T11:59:55.000Z');

    // Raw query string is percent-encoded (colons must be %3A).
    expect(url.search).toBe('?startTime=2026-01-15T11%3A59%3A55.000Z');
  });

  it('percent-encodes the sensorId path segment', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      jsonResponse([
        { name: 'cam', sensorId: 'id with space/slash', state: 'online' },
      ]),
    );

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
    global.fetch = jest.fn().mockResolvedValue(
      jsonResponse([{ name: 'cam', sensorId: 'id-1', state: 'online' }]),
    );

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
});

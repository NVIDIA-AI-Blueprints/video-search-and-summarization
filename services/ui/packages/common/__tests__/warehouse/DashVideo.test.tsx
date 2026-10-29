// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

import {
  formatReplayTimestring,
  parseReplayWindowMs,
} from '../../lib-src/warehouse/mdat/components/generic/DashVideo/DashVideoTimeline';

const start = jest.fn();
const stop = jest.fn();
const seekReplay = jest.fn();
const dashVideoEl = document.createElement('video');

jest.mock(
  'vst-streaming-lib/dash',
  () => ({
    DashStream: jest.fn().mockImplementation(() => ({
      start,
      stop,
      seekReplay,
    })),
  }),
  { virtual: true },
);

jest.mock('../../lib-src/warehouse/mdat/components/generic/VideoElement/VideoElement', () => {
  const ReactLib = require('react');
  class VideoElement extends ReactLib.Component {
    VIDEO_CONTROL_PAUSE_LABEL = 'Pause';
    VIDEO_CONTROL_PLAY_LABEL = 'Play';
    video = dashVideoEl;
    switchPlayPauseBtn() {}
    render() {
      return ReactLib.createElement('div', { 'data-testid': 'warehouse-video-element' });
    }
  }
  return { __esModule: true, default: VideoElement };
});

jest.mock('common/warehouse/mui', () => {
  const ReactLib = require('react');
  return {
    Tooltip: ({ children }: { children: React.ReactNode }) =>
      ReactLib.createElement(ReactLib.Fragment, null, children),
  };
});

import DashVideo from '../../lib-src/warehouse/mdat/components/generic/DashVideo/DashVideo';

describe('parseReplayWindowMs', () => {
  it('returns start and end milliseconds for a recorded window', () => {
    expect(
      parseReplayWindowMs('2026-01-01T00:00:00.000Z', '2026-01-01T00:01:00.000Z'),
    ).toEqual({
      startMs: Date.parse('2026-01-01T00:00:00.000Z'),
      endMs: Date.parse('2026-01-01T00:01:00.000Z'),
    });
  });

  it('returns null for live sessions without a window', () => {
    expect(parseReplayWindowMs(undefined, undefined)).toBeNull();
  });
});

describe('formatReplayTimestring', () => {
  it('keeps the source timestring when it matches the instant', () => {
    const source = '2026-01-01T00:00:00.000Z';
    expect(formatReplayTimestring(Date.parse(source), source)).toBe(source);
  });

  it('falls back to ISO when no source timestring matches', () => {
    expect(formatReplayTimestring(Date.parse('2026-01-01T00:00:20.000Z'))).toBe(
      '2026-01-01T00:00:20.000Z',
    );
  });
});

describe('DashVideo recorded seek', () => {
  const startTime = '2026-01-01T00:00:00.000Z';
  const endTime = '2026-01-01T00:01:00.000Z';
  const seekTime = '2026-01-01T00:00:30.000Z';

  beforeEach(() => {
    dashVideoEl.currentTime = 0;
    Object.defineProperty(dashVideoEl, 'duration', {
      configurable: true,
      get: () => Number.NaN,
    });
    Object.defineProperty(dashVideoEl, 'ended', {
      configurable: true,
      get: () => false,
    });
    Object.defineProperty(dashVideoEl, 'readyState', {
      configurable: true,
      get: () => 4,
    });
    Object.defineProperty(dashVideoEl, 'seekable', {
      configurable: true,
      value: { length: 0, start: () => 0, end: () => 0 },
    });
    Object.defineProperty(dashVideoEl, 'buffered', {
      configurable: true,
      value: { length: 0, start: () => 0, end: () => 0 },
    });
    start.mockImplementation(async (config: { onFirstFrame?: () => void }) => {
      config.onFirstFrame?.();
      return { viewerId: 'viewer-1' };
    });
    stop.mockResolvedValue(undefined);
    seekReplay.mockResolvedValue({ viewerId: 'viewer-2' });
  });

  it('does not show a seek bar for live DASH', async () => {
    render(
      <DashVideo
        videoConnectionInfo={{
          endpoint: 'http://example.test',
          streamId: 'cam-1',
        }}
        htmlVideoTagOptions={{ width: '100%', height: '100%' }}
      />,
    );
    await waitFor(() => expect(start).toHaveBeenCalled());
    expect(screen.queryByLabelText('Seek recorded video')).not.toBeInTheDocument();
  });

  it('does not show the seek bar until the first frame', async () => {
    start.mockImplementation(async () => ({ viewerId: 'viewer-1' }));
    render(
      <DashVideo
        videoConnectionInfo={{
          endpoint: 'http://example.test',
          streamId: 'cam-1',
          startTime,
          endTime,
        }}
        htmlVideoTagOptions={{ width: '100%', height: '100%' }}
        videoControls={{ playpause: true }}
      />,
    );
    await waitFor(() => expect(start).toHaveBeenCalled());
    expect(screen.queryByLabelText('Seek recorded video')).not.toBeInTheDocument();
  });

  it('labels start/end markers and shows pointer/current time bubbles on hover', async () => {
    render(
      <DashVideo
        videoConnectionInfo={{
          endpoint: 'http://example.test',
          streamId: 'cam-1',
          startTime,
          endTime,
        }}
        htmlVideoTagOptions={{ width: '100%', height: '100%' }}
        videoControls={{ playpause: true }}
      />,
    );
    await screen.findByLabelText('Seek recorded video');
    expect(screen.getByLabelText(`Start ${startTime}`)).toBeInTheDocument();
    expect(screen.getByLabelText(`End ${endTime}`)).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();

    const slider = screen.getByLabelText('Seek recorded video');
    jest.spyOn(slider, 'getBoundingClientRect').mockReturnValue({
      left: 0, top: 0, right: 216, bottom: 14, width: 216, height: 14, x: 0, y: 0,
      toJSON: () => ({}),
    });

    // Over the thumb (at the start of the bar): current-time bubble.
    fireEvent.mouseMove(slider, { clientX: 8 });
    expect(screen.getByLabelText(`Current time ${startTime}`)).toHaveTextContent(startTime);

    // Elsewhere on the bar: bubble with the time under the pointer.
    fireEvent.mouseMove(slider, { clientX: 208 });
    expect(screen.getByLabelText(`Time at pointer ${endTime}`)).toHaveTextContent(endTime);
    expect(screen.queryByLabelText(/^Current time/)).not.toBeInTheDocument();

    fireEvent.mouseLeave(slider);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('scrubs recorded DASH inside the current clip without VST seekReplay', async () => {
    render(
      <DashVideo
        videoConnectionInfo={{
          endpoint: 'http://example.test',
          streamId: 'cam-1',
          startTime,
          endTime,
        }}
        htmlVideoTagOptions={{ width: '100%', height: '100%' }}
        videoControls={{ playpause: true }}
      />,
    );

    const slider = await screen.findByLabelText('Seek recorded video');
    fireEvent.pointerDown(slider);
    fireEvent.change(slider, { target: { value: String(Date.parse(seekTime)) } });
    fireEvent.pointerUp(slider);

    await waitFor(() => {
      expect(dashVideoEl.currentTime).toBe(30);
    });
    expect(seekReplay).not.toHaveBeenCalled();
  });

  it('uses the requested start/end window when media duration is unknown', async () => {
    render(
      <DashVideo
        videoConnectionInfo={{
          endpoint: 'http://example.test',
          streamId: 'cam-1',
          startTime,
          endTime,
        }}
        htmlVideoTagOptions={{ width: '100%', height: '100%' }}
        videoControls={{ playpause: true }}
      />,
    );
    const slider = await screen.findByLabelText('Seek recorded video');
    expect(slider).toHaveAttribute('min', String(Date.parse(startTime)));
    expect(slider).toHaveAttribute('max', String(Date.parse(endTime)));
  });

  it('uses media duration for the timeline when the element reports it', async () => {
    Object.defineProperty(dashVideoEl, 'duration', {
      configurable: true,
      get: () => 30,
    });
    render(
      <DashVideo
        videoConnectionInfo={{
          endpoint: 'http://example.test',
          streamId: 'cam-1',
          startTime,
          endTime,
        }}
        htmlVideoTagOptions={{ width: '100%', height: '100%' }}
        videoControls={{ playpause: true }}
      />,
    );
    const slider = await screen.findByLabelText('Seek recorded video');
    fireEvent(dashVideoEl, new Event('durationchange'));
    await waitFor(() => {
      expect(slider).toHaveAttribute('max', String(Date.parse(startTime) + 30_000));
    });
  });

  it('snaps the playhead to the timeline end when playback ends', async () => {
    Object.defineProperty(dashVideoEl, 'duration', {
      configurable: true,
      get: () => 30,
    });
    Object.defineProperty(dashVideoEl, 'ended', {
      configurable: true,
      get: () => true,
    });
    dashVideoEl.currentTime = 29.1;
    render(
      <DashVideo
        videoConnectionInfo={{
          endpoint: 'http://example.test',
          streamId: 'cam-1',
          startTime,
          endTime,
        }}
        htmlVideoTagOptions={{ width: '100%', height: '100%' }}
        videoControls={{ playpause: true }}
      />,
    );
    const slider = await screen.findByLabelText('Seek recorded video');
    fireEvent(dashVideoEl, new Event('ended'));
    await waitFor(() => {
      expect(Number(slider.getAttribute('value'))).toBe(Date.parse(startTime) + 30_000);
    });
  });

  it('uses the buffered range when duration is not finite', async () => {
    Object.defineProperty(dashVideoEl, 'duration', {
      configurable: true,
      get: () => Infinity,
    });
    Object.defineProperty(dashVideoEl, 'seekable', {
      configurable: true,
      value: {
        length: 1,
        start: () => 0,
        end: () => 60,
      },
    });
    Object.defineProperty(dashVideoEl, 'buffered', {
      configurable: true,
      value: {
        length: 1,
        start: () => 0,
        end: () => 20,
      },
    });
    render(
      <DashVideo
        videoConnectionInfo={{
          endpoint: 'http://example.test',
          streamId: 'cam-1',
          startTime,
          endTime,
        }}
        htmlVideoTagOptions={{ width: '100%', height: '100%' }}
        videoControls={{ playpause: true }}
      />,
    );
    const slider = await screen.findByLabelText('Seek recorded video');
    fireEvent(dashVideoEl, new Event('durationchange'));
    await waitFor(() => {
      expect(slider).toHaveAttribute('max', String(Date.parse(startTime) + 20_000));
    });
  });

  it('collapses the bar to the playhead when live-profile media runs out', async () => {
    Object.defineProperty(dashVideoEl, 'duration', {
      configurable: true,
      get: () => Infinity,
    });
    Object.defineProperty(dashVideoEl, 'ended', {
      configurable: true,
      get: () => false,
    });
    Object.defineProperty(dashVideoEl, 'paused', {
      configurable: true,
      get: () => false,
    });
    Object.defineProperty(dashVideoEl, 'buffered', {
      configurable: true,
      value: {
        length: 1,
        start: () => 0,
        end: () => 20.1,
      },
    });
    dashVideoEl.currentTime = 20;
    render(
      <DashVideo
        videoConnectionInfo={{
          endpoint: 'http://example.test',
          streamId: 'cam-1',
          startTime,
          endTime,
        }}
        htmlVideoTagOptions={{ width: '100%', height: '100%' }}
        videoControls={{ playpause: true }}
      />,
    );
    const slider = await screen.findByLabelText('Seek recorded video');
    fireEvent(dashVideoEl, new Event('waiting'));
    await waitFor(() => {
      expect(slider).toHaveAttribute('max', String(Date.parse(startTime) + 20_000));
      expect(Number(slider.getAttribute('value'))).toBe(Date.parse(startTime) + 20_000);
    });
  });

  it('collapses the bar to the playhead when playback stalls short of buffered media', async () => {
    jest.useFakeTimers();
    Object.defineProperty(dashVideoEl, 'duration', {
      configurable: true,
      get: () => Infinity,
    });
    Object.defineProperty(dashVideoEl, 'ended', {
      configurable: true,
      get: () => false,
    });
    Object.defineProperty(dashVideoEl, 'paused', {
      configurable: true,
      get: () => false,
    });
    Object.defineProperty(dashVideoEl, 'buffered', {
      configurable: true,
      value: {
        length: 1,
        start: () => 0,
        end: () => 28,
      },
    });
    dashVideoEl.currentTime = 20;
    render(
      <DashVideo
        videoConnectionInfo={{
          endpoint: 'http://example.test',
          streamId: 'cam-1',
          startTime,
          endTime,
        }}
        htmlVideoTagOptions={{ width: '100%', height: '100%' }}
        videoControls={{ playpause: true }}
      />,
    );
    const slider = await screen.findByLabelText('Seek recorded video');
    act(() => {
      jest.advanceTimersByTime(1100);
    });
    expect(slider).toHaveAttribute('max', String(Date.parse(startTime) + 20_000));
    expect(Number(slider.getAttribute('value'))).toBe(Date.parse(startTime) + 20_000);
    jest.useRealTimers();
  });
});

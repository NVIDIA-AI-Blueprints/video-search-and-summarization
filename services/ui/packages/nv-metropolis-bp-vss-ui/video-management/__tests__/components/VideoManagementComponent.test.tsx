// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { VideoManagementComponent } from '../../lib-src/VideoManagementComponent';
import { videoStream, rtspStream } from '../helpers/streamFixtures';

const mockOpenVideoModal = jest.fn(() => Promise.resolve());
const mockCloseVideoModal = jest.fn();

jest.mock('@nemo-agent-toolkit/ui', () => ({
  UploadFilesDialog: () => null,
  VideoModal: ({ isOpen, title }: { isOpen: boolean; title: string }) =>
    isOpen ? <div data-testid="video-modal">{title}</div> : null,
  useVideoModal: () => ({
    videoModal: { isOpen: false, videoUrl: '', title: '' },
    openVideoModal: mockOpenVideoModal,
    closeVideoModal: mockCloseVideoModal,
    openVideoModalFromUrl: jest.fn(),
    openVideoModalFromAlert: jest.fn(),
    loadingAlertId: null,
  }),
  copyToClipboard: jest.fn(),
}));

jest.mock('../../lib-src/chunkedUpload', () => ({
  uploadFileChunked: jest.fn().mockResolvedValue({ sensorId: 'mock-sensor' }),
  notifyUploadComplete: jest.fn().mockResolvedValue(undefined),
}));

const mockTimelines = new Map([
  ['vid-1', {
    sizeInMegabytes: 100,
    state: 'active',
    timelines: [
      { startTime: '2025-01-01T00:00:00Z', endTime: '2025-01-01T00:03:30Z', sizeInMegabytes: 50 },
      { startTime: '2025-01-01T01:00:00Z', endTime: '2025-01-01T01:03:30Z', sizeInMegabytes: 50 },
    ],
  }],
  ['rtsp-1', {
    sizeInMegabytes: 200,
    state: 'active',
    timelines: [
      { startTime: '2025-01-01T00:00:00Z', endTime: '2025-01-01T12:00:00Z', sizeInMegabytes: 200 },
    ],
  }],
]);

jest.mock('../../lib-src/hooks', () => ({
  useStreams: () => ({
    streams: [videoStream, rtspStream],
    isLoading: false,
    error: null,
    refetch: jest.fn(),
  }),
  useStorageTimelines: () => ({
    timelines: mockTimelines,
    isLoading: false,
    error: null,
    refetch: jest.fn(),
    getEndTimeForStream: jest.fn(() => '2025-01-01T01:03:25Z'),
    getTimelineRangeForStream: jest.fn((streamId: string) => {
      if (streamId === 'vid-1') return { startTime: '2025-01-01T00:00:00Z', endTime: '2025-01-01T01:03:30Z' };
      return null;
    }),
    getLastTimelineForStream: jest.fn((streamId: string) => {
      const info = mockTimelines.get(streamId);
      if (!info?.timelines?.length) return null;
      const last = info.timelines[info.timelines.length - 1];
      return { startTime: last.startTime, endTime: last.endTime };
    }),
  }),
}));

jest.mock('../../lib-src/utils', () => {
  const actual = jest.requireActual('../../lib-src/utils');
  return {
    ...actual,
    fetchPictureWithQueue: jest.fn(() => Promise.reject(new Error('no thumbnail'))),
  };
});

jest.mock('../../lib-src/api', () => ({
  createApiEndpoints: () => ({
    LIVE_PICTURE: jest.fn(),
    REPLAY_PICTURE: jest.fn(),
  }),
}));

jest.mock('@tabler/icons-react', () => ({
  IconCheck: () => <span data-testid="icon-check" />,
  IconCopy: () => <span data-testid="icon-copy" />,
}));

const defaultProps = {
  videoManagementData: {
    systemStatus: 'ok',
    vstApiUrl: 'https://vst.example.com/vst',
    agentApiUrl: 'https://agent.example.com',
  },
};

function renderComponent(props: Partial<Parameters<typeof VideoManagementComponent>[0]> = {}) {
  return render(<VideoManagementComponent {...defaultProps} {...props} />);
}

describe('VideoManagementComponent — video playback', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders play buttons for all streams', async () => {
    renderComponent();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: `Play ${videoStream.name}` })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: `Play ${rtspStream.name}` })).toBeInTheDocument();
    });
  });

  it('calls openVideoModal with last timeline segment for uploaded video', async () => {
    renderComponent();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: `Play ${videoStream.name}` })).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: `Play ${videoStream.name}` }));
    });

    expect(mockOpenVideoModal).toHaveBeenCalledTimes(1);
    const callArgs = mockOpenVideoModal.mock.calls[0][0];
    expect(callArgs.video_name).toBe('test_video');
    expect(callArgs.sensor_id).toBe('sensor-vid');
    expect(callArgs.start_time).toBe('2025-01-01T01:00:00Z');
    expect(callArgs.end_time).toBe('2025-01-01T01:00:30.000Z');
  });

  it('calls openVideoModal with recent 30s window for RTSP stream', async () => {
    const fixedNow = new Date('2025-06-15T10:00:00Z').getTime();
    const realDate = global.Date;
    const mockDate = class extends realDate {
      constructor(...args: any[]) {
        if (args.length === 0) {
          super(fixedNow);
        } else {
          // @ts-ignore
          super(...args);
        }
      }

      static now() {
        return fixedNow;
      }
    };
    global.Date = mockDate as any;

    try {
      renderComponent();

      await waitFor(() => {
        expect(screen.getByRole('button', { name: `Play ${rtspStream.name}` })).toBeInTheDocument();
      });

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: `Play ${rtspStream.name}` }));
      });

      expect(mockOpenVideoModal).toHaveBeenCalledTimes(1);
      const callArgs = mockOpenVideoModal.mock.calls[0][0];
      expect(callArgs.video_name).toBe('Camera 1');
      expect(callArgs.sensor_id).toBe('sensor-rtsp');

      const expectedEnd = new realDate(fixedNow - 5000);
      const expectedStart = new realDate(fixedNow - 35000);
      expect(callArgs.start_time).toBe(expectedStart.toISOString());
      expect(callArgs.end_time).toBe(expectedEnd.toISOString());
    } finally {
      global.Date = realDate;
    }
  });

  it('renders VideoModal component', () => {
    renderComponent();

    expect(screen.queryByTestId('video-modal')).not.toBeInTheDocument();
  });
});

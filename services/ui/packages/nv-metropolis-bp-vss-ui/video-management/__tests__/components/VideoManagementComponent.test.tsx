// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { VideoManagementComponent } from '../../lib-src/VideoManagementComponent';
import { videoStream, rtspStream } from '../helpers/streamFixtures';

const mockOpenVideoModal = jest.fn(() => Promise.resolve());
const mockCloseVideoModal = jest.fn();
const mockChunkedUpload = jest.fn().mockResolvedValue({ sensorId: 'mock-sensor' });
const mockNotifyUploadComplete = jest.fn().mockResolvedValue(undefined);
let lastUploadDialogProps: any = null;
let lastUploadProgressPopupProps: any = null;
let lastUploadSuccessPopupProps: any = null;

jest.mock('@nemo-agent-toolkit/ui', () => ({
  UploadFilesDialog: (props: any) => {
    lastUploadDialogProps = props;
    return null;
  },
  UploadProgressPopup: (props: any) => {
    lastUploadProgressPopupProps = props;
    return <div data-testid="upload-progress-popup" />;
  },
  UploadSuccessPopup: (props: any) => {
    lastUploadSuccessPopupProps = props;
    return <div data-testid="upload-success-popup" />;
  },
  useChatVideoUploadCompleteSubscription: jest.fn(),
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
  chunkedUpload: (...args: any[]) => mockChunkedUpload(...args),
  notifyUploadComplete: (...args: any[]) => mockNotifyUploadComplete(...args),
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
    UPLOAD_FILE: 'https://vst.example.com/vst/v1/storage/file',
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
    lastUploadDialogProps = null;
    lastUploadProgressPopupProps = null;
    lastUploadSuccessPopupProps = null;
  });

  it('renders play buttons for all streams', async () => {
    renderComponent();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: `Play ${videoStream.name}` })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: `Play ${rtspStream.name}` })).toBeInTheDocument();
    });
  });

  it('calls openVideoModal with full last timeline segment for uploaded video', async () => {
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
    expect(callArgs.end_time).toBe('2025-01-01T01:03:30Z');
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

  it('uses uploadFilename from dialog for VST upload and complete notification', async () => {
    renderComponent();

    expect(lastUploadDialogProps).toBeTruthy();
    const file = new File(['123'], 'wh_test_6.mp4', { type: 'video/mp4' });

    await act(async () => {
      lastUploadDialogProps.onConfirm([
        {
          id: 'entry-1',
          file,
          uploadFilename: 'renamed_video.mp4',
          formData: { embedding: false },
        },
      ]);
    });

    await waitFor(() => {
      expect(mockChunkedUpload).toHaveBeenCalledWith(
        expect.objectContaining({
          file,
          fileName: 'renamed_video.mp4',
        }),
      );
    });

    await waitFor(() => {
      expect(mockNotifyUploadComplete).toHaveBeenCalledWith(
        'https://agent.example.com',
        'renamed_video.mp4',
        expect.any(Object),
        { embedding: false },
        expect.any(AbortSignal),
      );
    });
  });

  it('uses uploadFilename as-is without appending extension (matches Chat behavior)', async () => {
    renderComponent();

    expect(lastUploadDialogProps).toBeTruthy();
    const file = new File(['123'], 'wh_test_6.mp4', { type: 'video/mp4' });

    await act(async () => {
      lastUploadDialogProps.onConfirm([
        {
          id: 'entry-2',
          file,
          uploadFilename: 'renamed_video',
          formData: {},
        },
      ]);
    });

    await waitFor(() => {
      expect(mockChunkedUpload).toHaveBeenCalledWith(
        expect.objectContaining({
          file,
          fileName: 'renamed_video',
        }),
      );
    });
  });

  it('falls back to file.name when uploadFilename is empty', async () => {
    renderComponent();

    const file = new File(['123'], 'original_name.mp4', { type: 'video/mp4' });

    await act(async () => {
      lastUploadDialogProps.onConfirm([
        {
          id: 'entry-fallback',
          file,
          uploadFilename: '',
          formData: {},
        },
      ]);
    });

    await waitFor(() => {
      expect(mockChunkedUpload).toHaveBeenCalledWith(
        expect.objectContaining({
          file,
          fileName: 'original_name.mp4',
        }),
      );
    });
  });

  it('passes onCancelSingle to UploadProgressPopup for per-file cancel', async () => {
    mockChunkedUpload.mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve({ sensorId: 'mock-sensor' }), 500)),
    );

    renderComponent();
    const file = new File(['data'], 'slow.mp4', { type: 'video/mp4' });

    await act(async () => {
      lastUploadDialogProps.onConfirm([
        { id: 'cancel-test', file, uploadFilename: 'slow', formData: {} },
      ]);
    });

    await waitFor(() => {
      expect(lastUploadProgressPopupProps).toBeTruthy();
      expect(typeof lastUploadProgressPopupProps.onCancelSingle).toBe('function');
      expect(typeof lastUploadProgressPopupProps.onCancelAll).toBe('function');
    });

    mockChunkedUpload.mockResolvedValue({ sensorId: 'mock-sensor' });
  });

  it('renders common progress and success popups in upload flow', async () => {
    renderComponent();
    const file = new File(['123'], 'demo.mp4', { type: 'video/mp4' });

    await act(async () => {
      lastUploadDialogProps.onConfirm([
        {
          id: 'entry-3',
          file,
          uploadFilename: 'demo_renamed.mp4',
          formData: {},
        },
      ]);
    });

    await waitFor(() => {
      expect(screen.getByTestId('upload-success-popup')).toBeInTheDocument();
      expect(lastUploadSuccessPopupProps?.results).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            filename: 'demo_renamed.mp4',
          }),
        ]),
      );
    });
  });
});

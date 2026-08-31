// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { VideoManagementComponent } from '../../lib-src/VideoManagementComponent';
import { videoStream, rtspStream } from '../helpers/streamFixtures';

const mockOpenVideoModal = jest.fn(() => Promise.resolve());
const mockCloseVideoModal = jest.fn();
const mockChunkedUpload = jest.fn().mockResolvedValue({ sensorId: 'mock-sensor' });
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

// Mutable so a test can simulate VST dropping a stream and it reappearing later
let mockStreamsList = [videoStream, rtspStream];
const mockRefetch = jest.fn(() => Promise.resolve());
const mockWaitUntilStreamsRemoved = jest.fn(async () => ({ remainingSensorIds: [] as string[] }));
const mockDeleteRtspStream = jest.fn(() => Promise.resolve({ status: 'success' }));
const mockDeleteVideo = jest.fn(() => Promise.resolve({ status: 'success' }));

jest.mock('../../lib-src/rtspStream', () => ({
  deleteRtspStream: (...args: unknown[]) => mockDeleteRtspStream(...(args as [])),
}));

jest.mock('../../lib-src/videoDelete', () => ({
  deleteVideo: (...args: unknown[]) => mockDeleteVideo(...(args as [])),
}));

jest.mock('../../lib-src/hooks', () => ({
  useStreams: () => ({
    streams: mockStreamsList,
    isLoading: false,
    error: null,
    refetch: mockRefetch,
    waitUntilStreamsRemoved: mockWaitUntilStreamsRemoved,
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

  it('uses uploadFilename from dialog for the direct VST upload', async () => {
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

// NVBug 6243148: selecting an RTSP stream and an uploaded video together and
// hitting Select All → Delete left the RTSP entry in the grid, stale and
// unplayable, because VST's stream list still reported it when the UI refetched.
describe('VideoManagementComponent — Select All delete of mixed RTSP and uploaded videos', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockStreamsList = [videoStream, rtspStream];
    mockWaitUntilStreamsRemoved.mockResolvedValue({ remainingSensorIds: [] });
    mockDeleteRtspStream.mockResolvedValue({ status: 'success' });
    mockDeleteVideo.mockResolvedValue({ status: 'success' });
  });

  async function reopenDeleteDialogAndConfirm() {
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Select All' }));
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Delete Selected' }));
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId('delete-confirm-button'));
    });
  }

  async function selectAllAndConfirmDelete() {
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Select All' })).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Select All' }));
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Delete Selected' }));
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId('delete-confirm-button'));
    });
  }

  it('routes each selected stream to the delete API for its type', async () => {
    renderComponent();
    await selectAllAndConfirmDelete();

    expect(mockDeleteVideo).toHaveBeenCalledWith(
      'https://vst.example.com/vst',
      videoStream.sensorId,
      '2025-01-01T00:00:00Z',
      '2025-01-01T01:03:30Z',
    );
    expect(mockDeleteRtspStream).toHaveBeenCalledWith(
      'https://vst.example.com/vst',
      rtspStream.sensorId,
    );
  });

  it('waits for VST to stop listing deleted sensors before closing the dialog', async () => {
    renderComponent();
    await selectAllAndConfirmDelete();

    expect(mockWaitUntilStreamsRemoved).toHaveBeenCalledTimes(1);
    expect(mockWaitUntilStreamsRemoved.mock.calls[0][0]).toEqual(
      expect.arrayContaining([videoStream.sensorId, rtspStream.sensorId]),
    );
    expect(screen.queryByTestId('delete-confirm-dialog')).not.toBeInTheDocument();
  });

  it('keeps the dialog open with an error when VST still lists a deleted stream', async () => {
    mockWaitUntilStreamsRemoved.mockResolvedValueOnce({
      remainingSensorIds: [rtspStream.sensorId],
    });

    renderComponent();
    await selectAllAndConfirmDelete();

    expect(screen.getByTestId('delete-confirm-dialog')).toBeInTheDocument();
    // VST accepted this delete, so it must not be reported as a failure
    expect(screen.getByTestId('delete-confirm-error')).toHaveTextContent(
      `Deletion was accepted but these are still listed by VST: ${rtspStream.name}`,
    );
    expect(screen.getByTestId('delete-confirm-error')).not.toHaveTextContent(
      'Unable to remove the following streams',
    );
  });

  // A convergence timeout is not a failed delete: re-sending it would target a
  // sensor VST already removed.
  it('re-polls without re-sending the delete when VST has not converged yet', async () => {
    mockWaitUntilStreamsRemoved.mockResolvedValueOnce({
      remainingSensorIds: [rtspStream.sensorId],
    });

    renderComponent();
    await selectAllAndConfirmDelete();

    mockDeleteVideo.mockClear();
    mockDeleteRtspStream.mockClear();
    mockWaitUntilStreamsRemoved.mockClear();

    await act(async () => {
      fireEvent.click(screen.getByTestId('delete-confirm-button'));
    });

    expect(mockDeleteRtspStream).not.toHaveBeenCalled();
    expect(mockDeleteVideo).not.toHaveBeenCalled();
    expect(mockWaitUntilStreamsRemoved).toHaveBeenCalledWith([rtspStream.sensorId]);
    expect(screen.queryByTestId('delete-confirm-dialog')).not.toBeInTheDocument();
  });

  // Cancelling only dismisses the dialog — VST still accepted the delete, so
  // a later attempt must not repeat the destructive request.
  it('does not re-send the delete after the dialog is cancelled and reopened', async () => {
    mockWaitUntilStreamsRemoved.mockResolvedValueOnce({
      remainingSensorIds: [rtspStream.sensorId],
    });

    renderComponent();
    await selectAllAndConfirmDelete();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    });

    mockDeleteRtspStream.mockClear();
    mockDeleteVideo.mockClear();
    mockWaitUntilStreamsRemoved.mockClear();

    await reopenDeleteDialogAndConfirm();

    expect(mockDeleteRtspStream).not.toHaveBeenCalled();
    expect(mockWaitUntilStreamsRemoved).toHaveBeenCalledWith(
      expect.arrayContaining([rtspStream.sensorId]),
    );
  });

  // An acknowledgement belongs to the backend that gave it. A sensor id reused on a
  // different VST has not been deleted there, so the new backend must still get
  // the request instead of the dialog polling it to a timeout.
  it('re-sends the delete when the component is pointed at a different backend', async () => {
    mockWaitUntilStreamsRemoved.mockResolvedValueOnce({
      remainingSensorIds: [rtspStream.sensorId],
    });

    const { rerender } = renderComponent();
    await selectAllAndConfirmDelete();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    });

    await act(async () => {
      rerender(
        <VideoManagementComponent
          videoManagementData={{
            systemStatus: 'ok',
            vstApiUrl: 'https://other-vst.example.com/vst',
          }}
        />,
      );
    });

    mockDeleteRtspStream.mockClear();

    await reopenDeleteDialogAndConfirm();

    expect(mockDeleteRtspStream).toHaveBeenCalledWith(
      'https://other-vst.example.com/vst',
      rtspStream.sensorId,
    );
  });

  // Clearing on backend change is not enough on its own: a delete already awaiting its
  // VST response would otherwise record that answer afterwards, leaving the new
  // backend's identical sensor id looking accepted and its Retry stuck polling.
  it('discards an acknowledgement that arrives after the backend changed', async () => {
    let settleRtspDelete: (value: unknown) => void = () => {};
    mockDeleteRtspStream.mockImplementationOnce(
      () => new Promise((resolve) => {
        settleRtspDelete = resolve;
      }),
    );
    mockWaitUntilStreamsRemoved.mockResolvedValue({
      remainingSensorIds: [rtspStream.sensorId],
    });

    const { rerender } = renderComponent();
    await selectAllAndConfirmDelete();

    // The tab is pointed elsewhere while the VST call is still outstanding
    await act(async () => {
      rerender(
        <VideoManagementComponent
          videoManagementData={{
            systemStatus: 'ok',
            vstApiUrl: 'https://other-vst.example.com/vst',
          }}
        />,
      );
    });

    await act(async () => {
      settleRtspDelete({ status: 'success' });
    });

    mockDeleteRtspStream.mockClear();

    await act(async () => {
      fireEvent.click(screen.getByTestId('delete-confirm-button'));
    });

    expect(mockDeleteRtspStream).toHaveBeenCalledWith(
      'https://other-vst.example.com/vst',
      rtspStream.sensorId,
    );
  });

  // Forgetting a settled delete matters: a stream recreated under the same sensor
  // id must be deletable again rather than polled forever.
  it('re-sends the delete once VST has dropped the sensor and it reappears', async () => {
    mockWaitUntilStreamsRemoved.mockResolvedValueOnce({
      remainingSensorIds: [rtspStream.sensorId],
    });

    const { rerender } = renderComponent();
    await selectAllAndConfirmDelete();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    });

    // VST finally drops the stream, settling the delete
    mockStreamsList = [videoStream];
    await act(async () => {
      rerender(<VideoManagementComponent {...defaultProps} />);
    });

    // A new stream shows up reusing the same sensor id
    mockStreamsList = [videoStream, rtspStream];
    await act(async () => {
      rerender(<VideoManagementComponent {...defaultProps} />);
    });

    mockDeleteRtspStream.mockClear();

    await reopenDeleteDialogAndConfirm();

    expect(mockDeleteRtspStream).toHaveBeenCalledWith(
      'https://vst.example.com/vst',
      rtspStream.sensorId,
    );
  });

  it('keeps a failed stream selected and reports it without waiting for VST on that id', async () => {
    mockDeleteRtspStream.mockRejectedValueOnce(new Error('Stream not found in VST'));

    renderComponent();
    await selectAllAndConfirmDelete();

    // Only the successful video delete is waited on
    expect(mockWaitUntilStreamsRemoved).toHaveBeenCalledWith([videoStream.sensorId]);
    expect(screen.getByTestId('delete-confirm-dialog')).toBeInTheDocument();
    expect(screen.getByTestId('delete-confirm-error')).toHaveTextContent(rtspStream.name);
  });

  it('retries only the failed stream when the user confirms again', async () => {
    mockDeleteRtspStream.mockRejectedValueOnce(new Error('Stream not found in VST'));

    renderComponent();
    await selectAllAndConfirmDelete();

    mockDeleteVideo.mockClear();
    mockDeleteRtspStream.mockClear();
    mockWaitUntilStreamsRemoved.mockClear();

    await act(async () => {
      fireEvent.click(screen.getByTestId('delete-confirm-button'));
    });

    expect(mockDeleteRtspStream).toHaveBeenCalledTimes(1);
    expect(mockDeleteVideo).not.toHaveBeenCalled();
  });
});

// The RTSP and delete dialogs overlay the pane but not the toolbar above it, so their
// trigger buttons stay clickable while a dialog is open. A second dialog opened that way
// could end up stacked behind the first and be unreachable until the top one was closed.
describe('VideoManagementComponent — only one dialog at a time', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockStreamsList = [videoStream, rtspStream];
    lastUploadDialogProps = null;
  });

  const uploadButton = () => screen.getByRole('button', { name: '+ Upload Video' });
  const addRtspButton = () => screen.getByRole('button', { name: '+ Add RTSP' });

  it('blocks the upload dialog while the Add RTSP dialog is open', async () => {
    renderComponent();

    await act(async () => {
      fireEvent.click(addRtspButton());
    });
    expect(screen.getByTestId('add-rtsp-dialog')).toBeInTheDocument();

    expect(uploadButton()).toBeDisabled();
    await act(async () => {
      fireEvent.click(uploadButton());
    });

    expect(lastUploadDialogProps.open).toBe(false);
    expect(screen.getByTestId('add-rtsp-dialog')).toBeInTheDocument();
  });

  it('blocks the Add RTSP dialog while the upload dialog is open', async () => {
    renderComponent();

    await act(async () => {
      fireEvent.click(uploadButton());
    });
    expect(lastUploadDialogProps.open).toBe(true);

    expect(addRtspButton()).toBeDisabled();
    await act(async () => {
      fireEvent.click(addRtspButton());
    });

    expect(screen.queryByTestId('add-rtsp-dialog')).not.toBeInTheDocument();
    expect(lastUploadDialogProps.open).toBe(true);
  });

  it('re-enables the toolbar once the open dialog is closed', async () => {
    renderComponent();

    await act(async () => {
      fireEvent.click(addRtspButton());
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    });

    expect(screen.queryByTestId('add-rtsp-dialog')).not.toBeInTheDocument();
    expect(uploadButton()).not.toBeDisabled();

    await act(async () => {
      fireEvent.click(uploadButton());
    });
    expect(lastUploadDialogProps.open).toBe(true);
  });
});

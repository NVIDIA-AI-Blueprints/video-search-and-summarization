// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { StreamCard } from '../../lib-src/components/StreamCard';
import type { StreamInfo } from '../../lib-src/types';
import { makeStream, videoStream, rtspStream } from '../helpers/streamFixtures';

jest.mock('../../lib-src/utils', () => ({
  getFileExtension: (url: string) => {
    const parts = url.split('.');
    return parts.length > 1 ? parts.at(-1)!.toUpperCase() : '';
  },
  isRtspStream: (stream: StreamInfo) =>
    (stream.url ?? '').toLowerCase().startsWith('rtsp://'),
  fetchPictureWithQueue: jest.fn(() => Promise.reject(new Error('no thumbnail in test'))),
}));

jest.mock('../../lib-src/api', () => ({
  createApiEndpoints: () => ({
    LIVE_PICTURE: jest.fn(),
    REPLAY_PICTURE: jest.fn(),
  }),
}));

const mockCopyToClipboard = jest.fn(() => Promise.resolve());
jest.mock('@nemo-agent-toolkit/ui', () => ({
  copyToClipboard: (...args: unknown[]) => mockCopyToClipboard(...args),
}));

jest.mock('@tabler/icons-react', () => ({
  IconCheck: () => <span data-testid="icon-check" />,
}));

const defaultProps = {
  stream: videoStream,
  isSelected: false,
  onSelectionChange: jest.fn(),
  getEndTimeForStream: jest.fn(() => null),
};

function renderStreamCard(props: Partial<Parameters<typeof StreamCard>[0]> = {}) {
  return render(<StreamCard {...defaultProps} {...props} />);
}

describe('StreamCard — play button', () => {
  describe('visibility', () => {
    it('does not render play button when onPlay is not provided', () => {
      renderStreamCard();

      expect(screen.queryByRole('button', { name: /play/i })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /loading/i })).not.toBeInTheDocument();
    });

    it('renders play button when onPlay is provided for a video stream', () => {
      renderStreamCard({ onPlay: jest.fn() });

      expect(screen.getByRole('button', { name: `Play ${videoStream.name}` })).toBeInTheDocument();
    });

    it('renders play button when onPlay is provided for an RTSP stream', () => {
      renderStreamCard({ stream: rtspStream, onPlay: jest.fn() });

      expect(screen.getByRole('button', { name: `Play ${rtspStream.name}` })).toBeInTheDocument();
    });
  });

  describe('click behavior', () => {
    it('calls onPlay with the stream when play button is clicked', () => {
      const onPlay = jest.fn();
      renderStreamCard({ onPlay });

      fireEvent.click(screen.getByRole('button', { name: `Play ${videoStream.name}` }));

      expect(onPlay).toHaveBeenCalledTimes(1);
      expect(onPlay).toHaveBeenCalledWith(videoStream);
    });

    it('does not call onPlay when isLoadingPlay is true', () => {
      const onPlay = jest.fn();
      renderStreamCard({ onPlay, isLoadingPlay: true });

      fireEvent.click(screen.getByRole('button', { name: `Loading ${videoStream.name}` }));

      expect(onPlay).not.toHaveBeenCalled();
    });
  });

  describe('loading state', () => {
    it('shows "Loading" aria-label when isLoadingPlay is true', () => {
      renderStreamCard({ onPlay: jest.fn(), isLoadingPlay: true });

      expect(screen.getByRole('button', { name: `Loading ${videoStream.name}` })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: `Play ${videoStream.name}` })).not.toBeInTheDocument();
    });

    it('shows "Play" aria-label when isLoadingPlay is false', () => {
      renderStreamCard({ onPlay: jest.fn(), isLoadingPlay: false });

      expect(screen.getByRole('button', { name: `Play ${videoStream.name}` })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: `Loading ${videoStream.name}` })).not.toBeInTheDocument();
    });

    it('button is disabled when isLoadingPlay is true', () => {
      renderStreamCard({ onPlay: jest.fn(), isLoadingPlay: true });

      const btn = screen.getByRole('button', { name: `Loading ${videoStream.name}` });
      expect(btn).toBeDisabled();
    });

    it('button is enabled when isLoadingPlay is false', () => {
      renderStreamCard({ onPlay: jest.fn(), isLoadingPlay: false });

      const btn = screen.getByRole('button', { name: `Play ${videoStream.name}` });
      expect(btn).not.toBeDisabled();
    });
  });
});

describe('StreamCard — basic rendering', () => {
  it('displays stream name', () => {
    renderStreamCard();

    expect(screen.getByText(videoStream.name)).toBeInTheDocument();
  });

  it('displays codec and framerate metadata', () => {
    renderStreamCard();

    expect(screen.getByText('H264')).toBeInTheDocument();
    expect(screen.getByText('30 fps')).toBeInTheDocument();
  });

  it('displays MP4 label for video streams', () => {
    renderStreamCard();

    expect(screen.getByText('MP4')).toBeInTheDocument();
  });

  it('displays RTSP label for rtsp streams', () => {
    renderStreamCard({ stream: rtspStream });

    expect(screen.getByText('RTSP')).toBeInTheDocument();
  });

  it('checkbox reflects isSelected prop', () => {
    renderStreamCard({ isSelected: true });

    expect(screen.getByRole('checkbox')).toBeChecked();
  });

  it('calls onSelectionChange when checkbox is toggled', () => {
    const onSelectionChange = jest.fn();
    renderStreamCard({ isSelected: false, onSelectionChange });

    fireEvent.click(screen.getByRole('checkbox'));

    expect(onSelectionChange).toHaveBeenCalledWith(videoStream.streamId, true);
  });
});

describe('StreamCard — copy and chat context', () => {
  beforeEach(() => {
    mockCopyToClipboard.mockClear();
    mockCopyToClipboard.mockImplementation(() => Promise.resolve());
  });

  it('copies JSON to clipboard when + Chat is clicked', async () => {
    renderStreamCard();

    fireEvent.click(screen.getByRole('button', { name: /\+\s*chat/i }));

    await waitFor(() => {
      expect(mockCopyToClipboard).toHaveBeenCalledWith(
        JSON.stringify({ sensorName: videoStream.name, streamId: videoStream.streamId }, null, 2),
      );
    });
  });

  it('calls onAddChatQueryContext when + Chat is clicked', async () => {
    const onAddChatQueryContext = jest.fn();
    renderStreamCard({ onAddChatQueryContext });

    fireEvent.click(screen.getByRole('button', { name: /\+\s*chat/i }));

    expect(onAddChatQueryContext).toHaveBeenCalledTimes(1);
    expect(onAddChatQueryContext).toHaveBeenCalledWith({
      id: `video-mgmt-stream:${videoStream.streamId}`,
      label: videoStream.name,
      type: 'video-stream',
      data: { sensorName: videoStream.name, streamId: videoStream.streamId },
    });
    await waitFor(() => {
      expect(mockCopyToClipboard).toHaveBeenCalled();
    });
  });
});

// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { StreamsGrid } from '../../lib-src/components/StreamsGrid';
import type { StreamInfo } from '../../lib-src/types';

jest.mock('../../lib-src/components/StreamCard', () => ({
  StreamCard: () => <div data-testid="stream-card" />,
}));

const defaultMetadata = {
  bitrate: '',
  codec: 'H264',
  framerate: '30',
  govlength: '',
  resolution: '',
};

function makeStream(overrides: Partial<StreamInfo> & { name: string; streamId: string }): StreamInfo {
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

const defaultProps = {
  streams: [
    makeStream({ name: 'Stream A', streamId: 'id-a' }),
    makeStream({ name: 'Stream B', streamId: 'id-b' }),
    makeStream({ name: 'Stream C', streamId: 'id-c' }),
  ],
  selectedStreams: new Set<string>(),
  onSelectionChange: jest.fn(),
  onSelectAll: jest.fn(),
  showVideos: true,
  showRtsps: true,
  getEndTimeForStream: jest.fn(() => null),
};

function renderStreamsGrid(props: Partial<Parameters<typeof StreamsGrid>[0]> = {}) {
  return render(<StreamsGrid {...defaultProps} {...props} />);
}

describe('StreamsGrid', () => {
  describe('Select All / Deselect All visibility', () => {
    it('shows Select All and not Deselect All when no streams are selected', () => {
      renderStreamsGrid({ selectedStreams: new Set() });

      expect(screen.getByRole('button', { name: 'Select All' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Deselect All' })).not.toBeInTheDocument();
    });

    it('shows both Select All and Deselect All when a subset is selected', () => {
      renderStreamsGrid({ selectedStreams: new Set(['id-a']) });

      expect(screen.getByRole('button', { name: 'Select All' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Deselect All' })).toBeInTheDocument();
    });

    it('shows Deselect All and not Select All when all streams are selected', () => {
      renderStreamsGrid({
        selectedStreams: new Set(['id-a', 'id-b', 'id-c']),
      });

      expect(screen.queryByRole('button', { name: 'Select All' })).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Deselect All' })).toBeInTheDocument();
    });

    it('shows no Select All when streams list is empty (nothing to select)', () => {
      renderStreamsGrid({ streams: [], selectedStreams: new Set() });

      expect(screen.queryByRole('button', { name: 'Select All' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Deselect All' })).not.toBeInTheDocument();
    });
  });

  describe('Select All / Deselect All actions', () => {
    it('calls onSelectAll(true) when Select All is clicked', () => {
      const onSelectAll = jest.fn();
      renderStreamsGrid({ selectedStreams: new Set(), onSelectAll });

      fireEvent.click(screen.getByRole('button', { name: 'Select All' }));

      expect(onSelectAll).toHaveBeenCalledTimes(1);
      expect(onSelectAll).toHaveBeenCalledWith(true);
    });

    it('calls onSelectAll(false) when Deselect All is clicked', () => {
      const onSelectAll = jest.fn();
      renderStreamsGrid({
        selectedStreams: new Set(['id-a']),
        onSelectAll,
      });

      fireEvent.click(screen.getByRole('button', { name: 'Deselect All' }));

      expect(onSelectAll).toHaveBeenCalledTimes(1);
      expect(onSelectAll).toHaveBeenCalledWith(false);
    });
  });

  describe('header checkbox', () => {
    it('checkbox is unchecked when no streams are selected', () => {
      renderStreamsGrid({ selectedStreams: new Set() });

      const header = screen.getByRole('checkbox');
      expect(header).not.toBeChecked();
    });

    it('checkbox is checked when all streams are selected', () => {
      renderStreamsGrid({
        selectedStreams: new Set(['id-a', 'id-b', 'id-c']),
      });

      const header = screen.getByRole('checkbox');
      expect(header).toBeChecked();
    });

    it('checkbox is unchecked and not indeterminate when a subset is selected', () => {
      renderStreamsGrid({ selectedStreams: new Set(['id-a']) });

      const header = screen.getByRole('checkbox');
      expect(header).not.toBeChecked();
      expect((header as HTMLInputElement).indeterminate).toBe(false);
    });

    it('checkbox click when unchecked calls onSelectAll(true)', () => {
      const onSelectAll = jest.fn();
      renderStreamsGrid({ selectedStreams: new Set(), onSelectAll });

      fireEvent.click(screen.getByRole('checkbox'));

      expect(onSelectAll).toHaveBeenCalledWith(true);
    });

    it('checkbox click when all selected calls onSelectAll(false)', () => {
      const onSelectAll = jest.fn();
      renderStreamsGrid({
        selectedStreams: new Set(['id-a', 'id-b', 'id-c']),
        onSelectAll,
      });

      fireEvent.click(screen.getByRole('checkbox'));

      expect(onSelectAll).toHaveBeenCalledWith(false);
    });
  });
});

// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { VideoSearchList } from '../../lib-src/components/VideoSearchList';
import { SearchData } from '../../lib-src/types';

jest.mock('@nemo-agent-toolkit/ui');

const makeItem = (overrides: Partial<SearchData> = {}): SearchData => ({
  video_name: 'video-1.mp4',
  similarity: 0.85,
  screenshot_url: 'http://img.test/thumb1.jpg',
  description: 'A person walking',
  start_time: '2024-01-15T09:00:00',
  end_time: '2024-01-15T09:05:00',
  sensor_id: 'sensor-1',
  object_ids: ['1'],
  ...overrides,
});

describe('VideoSearchList', () => {
  const defaultProps = {
    data: [] as SearchData[],
    loading: false,
    error: null as string | null,
    isDark: false,
    onRefresh: jest.fn(),
    onPlayVideo: jest.fn(),
    showObjectsBbox: false,
  };

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('shows loading state with placeholder message', () => {
    render(<VideoSearchList {...defaultProps} loading={true} />);
    expect(screen.getByText('Results will update here')).toBeInTheDocument();
  });

  it('shows error state with error message and retry button', () => {
    render(<VideoSearchList {...defaultProps} error="Something went wrong" />);
    expect(screen.getByText('Error loading items')).toBeInTheDocument();
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(screen.getByText('Retry')).toBeInTheDocument();
  });

  it('calls onRefresh when retry button is clicked', () => {
    const onRefresh = jest.fn();
    render(<VideoSearchList {...defaultProps} error="Error occurred" onRefresh={onRefresh} />);

    fireEvent.click(screen.getByText('Retry'));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('shows empty state when data is empty', () => {
    render(<VideoSearchList {...defaultProps} data={[]} />);
    expect(screen.getByText('Results will update here')).toBeInTheDocument();
  });

  it('renders video cards for each search result', () => {
    const data = [
      makeItem({ video_name: 'clip-a.mp4', similarity: 0.95 }),
      makeItem({ video_name: 'clip-b.mp4', similarity: 0.80 }),
    ];

    render(<VideoSearchList {...defaultProps} data={data} />);

    expect(screen.getByText('clip-a.mp4')).toBeInTheDocument();
    expect(screen.getByText('clip-b.mp4')).toBeInTheDocument();
    expect(screen.getByText('0.95')).toBeInTheDocument();
    expect(screen.getByText('0.80')).toBeInTheDocument();
  });

  it('displays formatted time from start and end times', () => {
    const data = [makeItem({
      start_time: '2024-01-15T14:30:45',
      end_time: '2024-01-15T15:00:00',
    })];

    render(<VideoSearchList {...defaultProps} data={data} />);

    expect(screen.getByText('14:30:45')).toBeInTheDocument();
    expect(screen.getByText('15:00:00')).toBeInTheDocument();
  });

  it('calls onPlayVideo when play button is clicked', () => {
    const onPlayVideo = jest.fn();
    const item = makeItem();

    render(<VideoSearchList {...defaultProps} data={[item]} onPlayVideo={onPlayVideo} />);

    const playOverlays = document.querySelectorAll('.absolute.inset-0.flex');
    const clickableOverlay = Array.from(playOverlays).find(
      (el) => el.getAttribute('class')?.includes('items-center')
    );
    expect(clickableOverlay).toBeTruthy();
    fireEvent.click(clickableOverlay!);
    expect(onPlayVideo).toHaveBeenCalledWith(item, false);
  });

  it('passes showObjectsBbox to onPlayVideo', () => {
    const onPlayVideo = jest.fn();
    const item = makeItem();

    render(
      <VideoSearchList
        {...defaultProps}
        data={[item]}
        onPlayVideo={onPlayVideo}
        showObjectsBbox={true}
      />
    );

    const playOverlays = document.querySelectorAll('.absolute.inset-0.flex');
    const clickableOverlay = Array.from(playOverlays).find(
      (el) => el.getAttribute('class')?.includes('items-center')
    );
    expect(clickableOverlay).toBeTruthy();
    fireEvent.click(clickableOverlay!);
    expect(onPlayVideo).toHaveBeenCalledWith(item, true);
  });

  it('renders with dark mode styles', () => {
    const data = [makeItem()];
    const { container } = render(<VideoSearchList {...defaultProps} data={data} isDark={true} />);
    expect(container.querySelector('.text-gray-400')).toBeInTheDocument();
  });

  it('shows placeholder time for invalid dates', () => {
    const data = [makeItem({ start_time: '', end_time: '' })];
    render(<VideoSearchList {...defaultProps} data={data} />);

    const placeholders = screen.getAllByText('--:--:--');
    expect(placeholders.length).toBeGreaterThanOrEqual(2);
  });
});

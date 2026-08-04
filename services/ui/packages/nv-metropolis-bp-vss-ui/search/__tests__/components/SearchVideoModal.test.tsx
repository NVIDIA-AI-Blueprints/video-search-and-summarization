// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { SearchVideoModal } from '../../lib-src/components/SearchVideoModal';

jest.mock('@nemo-agent-toolkit/ui');

const setCurrentTime = (video: HTMLVideoElement, value: number) => {
  Object.defineProperty(video, 'currentTime', {
    value,
    writable: true,
    configurable: true,
  });
};

const defaultProps = {
  isOpen: true,
  videoUrl: 'http://vst.test/vst/clip.mp4',
  title: 'Clip',
  onClose: jest.fn(),
  searchByImageEnabled: true,
};

describe('SearchVideoModal Search by Image', () => {
  it('requests the offset of the frame currently shown when paused', () => {
    const onSearchByImageRequest = jest.fn();
    const { container } = render(
      <SearchVideoModal {...defaultProps} onSearchByImageRequest={onSearchByImageRequest} />
    );

    const video = container.querySelector('video') as HTMLVideoElement;
    setCurrentTime(video, 5);
    fireEvent.pause(video);

    fireEvent.click(screen.getByTestId('image-search-perform-button'));

    expect(onSearchByImageRequest).toHaveBeenCalledWith(5);
  });

  it('uses the scrubbed offset when the video is seeked while already paused', () => {
    const onSearchByImageRequest = jest.fn();
    const { container } = render(
      <SearchVideoModal {...defaultProps} onSearchByImageRequest={onSearchByImageRequest} />
    );

    const video = container.querySelector('video') as HTMLVideoElement;
    setCurrentTime(video, 5);
    fireEvent.pause(video);
    fireEvent.click(screen.getByTestId('image-search-perform-button'));

    // Scrubbing an already-paused video fires `seeked`, never `pause`.
    setCurrentTime(video, 12.5);
    fireEvent.seeked(video);
    fireEvent.click(screen.getByTestId('image-search-perform-button'));

    expect(onSearchByImageRequest).toHaveBeenLastCalledWith(12.5);
  });

  it('hides the Search by Image button while the video is playing', () => {
    render(<SearchVideoModal {...defaultProps} onSearchByImageRequest={jest.fn()} />);
    expect(screen.queryByTestId('image-search-perform-button')).not.toBeInTheDocument();
  });
});

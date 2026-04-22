// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { VideoModal } from '../../lib-src/components/VideoModal';

describe('VideoModal', () => {
  const defaultProps = {
    isOpen: true,
    videoUrl: 'http://example.com/video.mp4',
    title: 'Test Video',
    onClose: jest.fn(),
  };

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('returns null when isOpen is false', () => {
    const { container } = render(
      <VideoModal {...defaultProps} isOpen={false} />
    );

    expect(container.firstChild).toBeNull();
  });

  it('renders when isOpen is true', () => {
    render(<VideoModal {...defaultProps} />);

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Test Video')).toBeInTheDocument();
    expect(screen.getByLabelText('Close video')).toBeInTheDocument();
    expect(screen.getByRole('dialog').querySelector('video')).toBeInTheDocument();
  });

  it('renders video with correct source', () => {
    render(<VideoModal {...defaultProps} />);

    const video = screen.getByRole('dialog').querySelector('video');
    const source = video?.querySelector('source');
    expect(source).toHaveAttribute('src', 'http://example.com/video.mp4');
    expect(source).toHaveAttribute('type', 'video/mp4');
  });

  it('calls onClose when backdrop is clicked', () => {
    render(<VideoModal {...defaultProps} />);

    const backdrop = screen.getByRole('dialog');
    fireEvent.click(backdrop);

    expect(defaultProps.onClose).toHaveBeenCalled();
  });

  it('calls onClose when close button is clicked', () => {
    render(<VideoModal {...defaultProps} />);

    fireEvent.click(screen.getByLabelText('Close video'));

    expect(defaultProps.onClose).toHaveBeenCalled();
  });

  it('does not call onClose when modal content is clicked', () => {
    render(<VideoModal {...defaultProps} />);

    const content = screen.getByRole('dialog').querySelector('.relative');
    if (content) {
      fireEvent.click(content);
    }

    expect(defaultProps.onClose).not.toHaveBeenCalled();
  });
});

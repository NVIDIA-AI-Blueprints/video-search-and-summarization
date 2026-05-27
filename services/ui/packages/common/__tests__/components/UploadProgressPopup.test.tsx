// SPDX-License-Identifier: MIT
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { UploadProgressPopup } from '../../lib-src/components/UploadProgressPopup';

describe('UploadProgressPopup', () => {
  it('renders file names and statuses', () => {
    render(
      <UploadProgressPopup
        files={[
          { id: '1', displayName: 'video-1.mp4', uploadStatus: 'uploading', uploadProgress: 40 },
          { id: '2', displayName: 'video-2.mp4', uploadStatus: 'success', uploadProgress: 100 },
          { id: '3', displayName: 'video-3.mp4', uploadStatus: 'error', uploadError: 'network error' },
        ]}
        onCancelAll={jest.fn()}
        onCancelSingle={jest.fn()}
      />,
    );

    expect(screen.getByText('Uploading Files...')).toBeInTheDocument();
    expect(screen.getByText('video-1.mp4')).toBeInTheDocument();
    expect(screen.getByText('video-2.mp4')).toBeInTheDocument();
    expect(screen.getByText('video-3.mp4')).toBeInTheDocument();
    expect(screen.getByText('40%')).toBeInTheDocument();
    expect(screen.getByText('Done')).toBeInTheDocument();
    expect(screen.getByText('Failed')).toBeInTheDocument();
    expect(screen.getByText('network error')).toBeInTheDocument();
  });

  it('calls onCancelAll when Cancel All is clicked', () => {
    const onCancelAll = jest.fn();
    render(
      <UploadProgressPopup
        files={[{ id: '1', displayName: 'video-1.mp4', uploadStatus: 'uploading', uploadProgress: 10 }]}
        onCancelAll={onCancelAll}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Cancel All/i }));
    expect(onCancelAll).toHaveBeenCalledTimes(1);
  });

  it('calls onCancelSingle for active file item', () => {
    const onCancelSingle = jest.fn();
    render(
      <UploadProgressPopup
        files={[{ id: '1', displayName: 'video-1.mp4', uploadStatus: 'pending', uploadProgress: 0 }]}
        onCancelAll={jest.fn()}
        onCancelSingle={onCancelSingle}
      />,
    );

    fireEvent.click(screen.getByTitle('Cancel upload'));
    expect(onCancelSingle).toHaveBeenCalledWith('1');
  });

  it('hides Cancel All when all files are done', () => {
    render(
      <UploadProgressPopup
        files={[
          { id: '1', displayName: 'video-1.mp4', uploadStatus: 'success', uploadProgress: 100 },
          { id: '2', displayName: 'video-2.mp4', uploadStatus: 'error', uploadProgress: 50 },
        ]}
        onCancelAll={jest.fn()}
      />,
    );

    expect(screen.queryByRole('button', { name: /Cancel All/i })).not.toBeInTheDocument();
  });
});

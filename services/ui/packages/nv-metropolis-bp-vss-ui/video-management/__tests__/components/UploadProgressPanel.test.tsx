// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen } from '@testing-library/react';
import { UploadProgressPanel } from '../../lib-src/components/UploadProgressPanel';
import type { UploadProgress } from '../../lib-src/types';

const noop = () => {};

function uploadList(overrides: Partial<UploadProgress>[]): UploadProgress[] {
  return overrides.map((u, i) => ({
    id: u.id ?? `id-${i}`,
    fileName: u.fileName ?? `file-${i}.mp4`,
    progress: u.progress ?? 0,
    status: u.status ?? 'pending',
    error: u.error,
  }));
}

describe('UploadProgressPanel', () => {
  it('shows processing header when uploads are only in processing state', () => {
    render(
      <UploadProgressPanel
        uploads={uploadList([{ status: 'processing', fileName: 'a.mp4', progress: 100 }])}
        onClose={noop}
        onCancel={noop}
      />,
    );

    expect(screen.getByText('Processing 1 file...')).toBeInTheDocument();
  });

  it('pluralizes processing header for multiple processing uploads', () => {
    render(
      <UploadProgressPanel
        uploads={uploadList([
          { status: 'processing', fileName: 'a.mp4', progress: 100 },
          { status: 'processing', fileName: 'b.mp4', progress: 100 },
        ])}
        onClose={noop}
        onCancel={noop}
      />,
    );

    expect(screen.getByText('Processing 2 files...')).toBeInTheDocument();
  });

  it('shows per-row Processing label for processing status', () => {
    render(
      <UploadProgressPanel
        uploads={uploadList([{ status: 'processing', fileName: 'job.mp4', progress: 100 }])}
        onClose={noop}
        onCancel={noop}
      />,
    );

    expect(screen.getByText('Processing...')).toBeInTheDocument();
    expect(screen.getByText('job.mp4')).toBeInTheDocument();
  });

  it('prefers uploading header when both uploading and processing are present', () => {
    render(
      <UploadProgressPanel
        uploads={uploadList([
          { status: 'uploading', fileName: 'up.mp4', progress: 50 },
          { status: 'processing', fileName: 'done.mp4', progress: 100 },
        ])}
        onClose={noop}
        onCancel={noop}
      />,
    );

    expect(screen.queryByText(/Processing \d+ file/)).not.toBeInTheDocument();
    // Count includes both uploading and processing so the display only ever
    // grows as files advance (uploading → processing → success).
    expect(screen.getByText('Uploading 2/2 files...')).toBeInTheDocument();
  });
});

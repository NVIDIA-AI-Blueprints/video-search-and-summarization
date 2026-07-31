// SPDX-License-Identifier: MIT
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { UploadSuccessPopup } from '../../lib-src/components/UploadSuccessPopup';
import { copyToClipboard } from '../../lib-src/utils/clipboard';

jest.mock('../../lib-src/utils/clipboard', () => ({
  copyToClipboard: jest.fn(),
}));

const mockCopyToClipboard = copyToClipboard as jest.MockedFunction<typeof copyToClipboard>;

describe('UploadSuccessPopup', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('shows complete status for all successful uploads', () => {
    render(
      <UploadSuccessPopup
        results={[
          { filename: 'a.mp4', result: { id: 'a' } },
          { filename: 'b.mp4', result: { id: 'b' } },
        ]}
        onClose={jest.fn()}
      />,
    );

    expect(screen.getByText('Upload Complete!')).toBeInTheDocument();
    expect(screen.getByText('2 / 2 files uploaded successfully')).toBeInTheDocument();
  });

  it('shows partial status with cancelled and failed counts', () => {
    render(
      <UploadSuccessPopup
        results={[
          { filename: 'a.mp4', result: { id: 'a' } },
          { filename: 'b.mp4', cancelled: true },
          { filename: 'c.mp4', error: 'upload failed' },
        ]}
        onClose={jest.fn()}
      />,
    );

    expect(screen.getByText('Upload Partially Complete')).toBeInTheDocument();
    expect(screen.getByText('(1 cancelled)')).toBeInTheDocument();
    expect(screen.getByText('(1 failed)')).toBeInTheDocument();
  });

  it('expands item details and copies result JSON', async () => {
    mockCopyToClipboard.mockResolvedValue(true);
    render(
      <UploadSuccessPopup
        results={[{ filename: 'a.mp4', result: { sensor_id: 'abc-1' } }]}
        onClose={jest.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /a\.mp4/i }));
    expect(screen.getByText(/"sensor_id": "abc-1"/)).toBeInTheDocument();

    fireEvent.click(screen.getByTitle('Copy JSON'));
    await waitFor(() => {
      expect(mockCopyToClipboard).toHaveBeenCalledWith(expect.stringContaining('"sensor_id": "abc-1"'));
    });
  });

  it('calls onClose when close button is clicked', () => {
    const onClose = jest.fn();
    render(
      <UploadSuccessPopup
        results={[{ filename: 'a.mp4', result: { id: 'a' } }]}
        onClose={onClose}
      />,
    );

    fireEvent.click(screen.getByTestId('upload-close-button'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

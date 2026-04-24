// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { UploadFilesDialog } from '../../lib-src/components/UploadFilesDialog';
import type { UploadFileConfigTemplate } from '../../lib-src/types/uploadFileConfig';
import toast from 'react-hot-toast';
import { createMockFile, createFileList } from '../../test-helpers';

jest.mock('react-hot-toast', () => ({
  __esModule: true,
  default: {
    error: jest.fn(),
    success: jest.fn(),
  },
}));

const mockConfigTemplate: UploadFileConfigTemplate = {
  fields: [
    {
      'field-name': 'sensorId',
      'field-type': 'string',
      'field-default-value': 'test-sensor',
    },
  ],
};

const configWithAllFieldTypes: UploadFileConfigTemplate = {
  fields: [
    { 'field-name': 'enabled', 'field-type': 'boolean', 'field-default-value': true },
    {
      'field-name': 'quality',
      'field-type': 'select',
      'field-default-value': 'high',
      'field-options': ['low', 'medium', 'high'],
    },
    { 'field-name': 'fps', 'field-type': 'number', 'field-default-value': 30 },
    { 'field-name': 'sensorId', 'field-type': 'string', 'field-default-value': 's1' },
  ],
};

describe('UploadFilesDialog', () => {
  const defaultProps = {
    configTemplate: mockConfigTemplate,
    onClose: jest.fn(),
    onConfirm: jest.fn(),
  };

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('returns null when open is false', () => {
    const { container } = render(
      <UploadFilesDialog {...defaultProps} open={false} />
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders when open is true', () => {
    render(<UploadFilesDialog {...defaultProps} open={true} />);
    expect(screen.getByText('Upload Files')).toBeInTheDocument();
    expect(screen.getByText('Files')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Cancel/i })).toBeInTheDocument();
    expect(screen.getByTestId('upload-confirm-button')).toBeInTheDocument();
  });

  it('calls onClose when Cancel is clicked', () => {
    render(<UploadFilesDialog {...defaultProps} open={true} />);
    fireEvent.click(screen.getByRole('button', { name: /Cancel/i }));
    expect(defaultProps.onClose).toHaveBeenCalled();
  });

  it('disables Upload button when no files', () => {
    render(<UploadFilesDialog {...defaultProps} open={true} />);
    expect(screen.getByTestId('upload-confirm-button')).toBeDisabled();
  });

  it('shows empty drop zone when no files', () => {
    render(<UploadFilesDialog {...defaultProps} open={true} />);
    expect(screen.getByText(/Click or drag files here/i)).toBeInTheDocument();
  });

  it('uses custom title when provided in options', () => {
    render(
      <UploadFilesDialog
        {...defaultProps}
        open={true}
        options={{ title: 'Custom Upload' }}
      />
    );
    expect(screen.getByText('Custom Upload')).toBeInTheDocument();
  });

  it('uses custom emptyStateHint when provided', () => {
    render(
      <UploadFilesDialog
        {...defaultProps}
        open={true}
        options={{ emptyStateHint: <span data-testid="custom-hint">Drop videos</span> }}
      />
    );
    expect(screen.getByTestId('custom-hint')).toBeInTheDocument();
  });

  it('renders with ref and imperative open', () => {
    const ref = React.createRef<any>();
    render(<UploadFilesDialog {...defaultProps} ref={ref} />);
    expect(screen.queryByText('Upload Files')).not.toBeInTheDocument();
    act(() => {
      ref.current?.open();
    });
    expect(screen.getByText('Upload Files')).toBeInTheDocument();
  });

  it('ref.close() closes dialog and calls onClose', () => {
    const ref = React.createRef<any>();
    render(<UploadFilesDialog {...defaultProps} ref={ref} />);
    act(() => ref.current?.open());
    expect(screen.getByText('Upload Files')).toBeInTheDocument();
    act(() => ref.current?.close());
    expect(defaultProps.onClose).toHaveBeenCalled();
    expect(screen.queryByText('Upload Files')).not.toBeInTheDocument();
  });

  it('adds files via file input change', () => {
    render(<UploadFilesDialog {...defaultProps} open={true} />);
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = createMockFile('video.mp4');
    fireEvent.change(fileInput, { target: { files: createFileList([file]) } });
    expect(screen.getByText('video.mp4')).toBeInTheDocument();
    expect(screen.getByTestId('upload-confirm-button')).not.toBeDisabled();
  });

  it('adds files via drag and drop on empty zone', () => {
    render(<UploadFilesDialog {...defaultProps} open={true} />);
    const dropZone = screen.getByText(/Click or drag files here/i).closest('button');
    const file = createMockFile('dropped.mp4');
    const dataTransfer = { files: [file], types: ['Files'] };
    fireEvent.drop(dropZone!, {
      dataTransfer,
      preventDefault: jest.fn(),
      stopPropagation: jest.fn(),
    });
    expect(screen.getByText('dropped.mp4')).toBeInTheDocument();
  });

  it('shows Add More button when files exist', () => {
    render(<UploadFilesDialog {...defaultProps} open={true} />);
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: createFileList([createMockFile()]) } });
    expect(screen.getByRole('button', { name: /Add More/i })).toBeInTheDocument();
  });

  it('shows "+ Add More" text when addMoreWithIcon is false', () => {
    render(
      <UploadFilesDialog
        {...defaultProps}
        open={true}
        options={{ addMoreWithIcon: false }}
      />
    );
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: createFileList([createMockFile()]) } });
    expect(screen.getByRole('button', { name: '+ Add More' })).toBeInTheDocument();
  });

  it('removes file when remove button is clicked', () => {
    render(<UploadFilesDialog {...defaultProps} open={true} />);
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: createFileList([createMockFile('a.mp4')]) } });
    expect(screen.getByText('a.mp4')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Remove file'));
    expect(screen.queryByText('a.mp4')).not.toBeInTheDocument();
  });

  it('shows toast when invalid file is dropped', () => {
    const validateFile = jest.fn(() => false);
    render(
      <UploadFilesDialog {...defaultProps} open={true} validateFile={validateFile} />
    );
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: createFileList([createMockFile('bad.txt')]) } });
    expect(toast.error).toHaveBeenCalledWith('Please drop video files only (mp4, mkv)');
  });

  it('seeds files from initialFiles in controlled mode', () => {
    const file = createMockFile('initial.mp4');
    render(
      <UploadFilesDialog
        {...defaultProps}
        open={true}
        initialFiles={[file]}
      />
    );
    expect(screen.getByText('initial.mp4')).toBeInTheDocument();
  });

  it('calls onConfirm with entries when Upload is clicked', () => {
    render(<UploadFilesDialog {...defaultProps} open={true} />);
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = createMockFile('my-video.mp4');
    fireEvent.change(fileInput, { target: { files: createFileList([file]) } });
    fireEvent.click(screen.getByText('my-video.mp4'));
    const filenameInput = screen.getByPlaceholderText('e.g. my-video');
    fireEvent.change(filenameInput, { target: { value: 'valid-name' } });
    fireEvent.click(screen.getByTestId('upload-confirm-button'));
    expect(defaultProps.onConfirm).toHaveBeenCalledWith(
      expect.arrayContaining([
        expect.objectContaining({
          file,
          formData: expect.objectContaining({ sensorId: 'test-sensor' }),
          uploadFilename: 'valid-name',
        }),
      ])
    );
    expect(defaultProps.onClose).toHaveBeenCalled();
  });

  it('disables Upload button when filename is invalid', () => {
    render(<UploadFilesDialog {...defaultProps} open={true} />);
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: createFileList([createMockFile()]) } });
    fireEvent.click(screen.getByText('test.mp4'));
    const filenameInput = screen.getByPlaceholderText('e.g. my-video');
    fireEvent.change(filenameInput, { target: { value: '' } });
    expect(screen.getByTestId('upload-confirm-button')).toBeDisabled();
  });

  it('expands file row and shows config fields when config has fields', () => {
    render(
      <UploadFilesDialog
        {...defaultProps}
        configTemplate={configWithAllFieldTypes}
        open={true}
        initialFiles={[createMockFile()]}
      />
    );
    fireEvent.click(screen.getByText('test.mp4'));
    expect(screen.getByText('Enabled')).toBeInTheDocument();
    expect(screen.getByDisplayValue('high')).toBeInTheDocument();
    expect(screen.getByDisplayValue('30')).toBeInTheDocument();
    expect(screen.getByDisplayValue('s1')).toBeInTheDocument();
  });

  it('toggles boolean field', () => {
    render(
      <UploadFilesDialog
        {...defaultProps}
        configTemplate={configWithAllFieldTypes}
        open={true}
        initialFiles={[createMockFile()]}
      />
    );
    fireEvent.click(screen.getByText('test.mp4'));
    const switchBtn = screen.getByRole('switch');
    expect(switchBtn).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(switchBtn);
    expect(switchBtn).toHaveAttribute('aria-checked', 'false');
  });

  it('changes select field', () => {
    render(
      <UploadFilesDialog
        {...defaultProps}
        configTemplate={configWithAllFieldTypes}
        open={true}
        initialFiles={[createMockFile()]}
      />
    );
    fireEvent.click(screen.getByText('test.mp4'));
    const select = screen.getByDisplayValue('high');
    fireEvent.change(select, { target: { value: 'low' } });
    expect(screen.getByDisplayValue('low')).toBeInTheDocument();
  });

  it('changes number field', () => {
    render(
      <UploadFilesDialog
        {...defaultProps}
        configTemplate={configWithAllFieldTypes}
        open={true}
        initialFiles={[createMockFile()]}
      />
    );
    fireEvent.click(screen.getByText('test.mp4'));
    const numInput = screen.getByDisplayValue('30');
    fireEvent.change(numInput, { target: { value: '60' } });
    expect(screen.getByDisplayValue('60')).toBeInTheDocument();
  });

  describe('metadata', () => {
    it('shows Metadata section when metadata.enabled is true', () => {
      render(
        <UploadFilesDialog
          {...defaultProps}
          open={true}
          initialFiles={[createMockFile()]}
          metadata={{ enabled: true }}
        />
      );
      fireEvent.click(screen.getByText('test.mp4'));
      expect(screen.getByText('Metadata (JSON)')).toBeInTheDocument();
    });

    it('validates metadata file and shows toast on invalid JSON', async () => {
      const validateMetadataFile = jest.fn().mockResolvedValue(false);
      const metaFile = new File(['x'], 'meta.json', { type: 'application/json' });
      render(
        <UploadFilesDialog
          {...defaultProps}
          open={true}
          initialFiles={[createMockFile()]}
          metadata={{ enabled: true, validateMetadataFile }}
        />
      );
      fireEvent.click(screen.getByText('test.mp4'));
      fireEvent.click(screen.getByText('Metadata (JSON)'));
      act(() => {
        fireEvent.click(screen.getByText('Click or drag JSON metadata'));
      });
      const metadataInput = document.querySelector('input[accept=".json,application/json"]') as HTMLInputElement;
      await act(async () => {
        fireEvent.change(metadataInput, { target: { files: createFileList([metaFile]), value: '' } });
      });
      expect(toast.error).toHaveBeenCalledWith('Invalid JSON format. Please check your file.');
    });

    it('accepts valid JSON metadata file', async () => {
      const validateMetadataFile = jest.fn().mockResolvedValue(true);
      const metaFile = new File(['{}'], 'meta.json', { type: 'application/json' });
      render(
        <UploadFilesDialog
          {...defaultProps}
          open={true}
          initialFiles={[createMockFile()]}
          metadata={{ enabled: true, validateMetadataFile }}
        />
      );
      fireEvent.click(screen.getByText('test.mp4'));
      fireEvent.click(screen.getByText('Metadata (JSON)'));
      act(() => {
        fireEvent.click(screen.getByText('Click or drag JSON metadata'));
      });
      const metadataInput = document.querySelector('input[accept=".json,application/json"]') as HTMLInputElement;
      await act(async () => {
        fireEvent.change(metadataInput, { target: { files: createFileList([metaFile]), value: '' } });
      });
      expect(screen.getByText('meta.json')).toBeInTheDocument();
    });

    it('removes metadata file when remove is clicked', async () => {
      const validateMetadataFile = jest.fn().mockResolvedValue(true);
      const metaFile = new File(['{}'], 'meta.json', { type: 'application/json' });
      render(
        <UploadFilesDialog
          {...defaultProps}
          open={true}
          initialFiles={[createMockFile()]}
          metadata={{ enabled: true, validateMetadataFile }}
        />
      );
      fireEvent.click(screen.getByText('test.mp4'));
      fireEvent.click(screen.getByText('Metadata (JSON)'));
      act(() => {
        fireEvent.click(screen.getByText('Click or drag JSON metadata'));
      });
      const metadataInput = document.querySelector('input[accept=".json,application/json"]') as HTMLInputElement;
      await act(async () => {
        fireEvent.change(metadataInput, { target: { files: createFileList([metaFile]), value: '' } });
      });
      expect(screen.getByText('meta.json')).toBeInTheDocument();
      const metaCard = screen.getByText('meta.json').closest('[class*="border-[#76b900]"]');
      const removeBtn = metaCard?.querySelector('button');
      if (removeBtn) fireEvent.click(removeBtn);
      expect(screen.queryByText('meta.json')).not.toBeInTheDocument();
    });
  });

  it('ref.open(files) seeds with provided files', () => {
    const ref = React.createRef<any>();
    const file = createMockFile('seeded.mp4');
    render(<UploadFilesDialog {...defaultProps} ref={ref} />);
    act(() => ref.current?.open([file]));
    expect(screen.getByText('seeded.mp4')).toBeInTheDocument();
  });
});

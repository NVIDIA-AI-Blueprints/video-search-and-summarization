/**
 * Tests that ChatFileUpload blocks upload triggers and drag feedback when disabled
 * (e.g. while a query is in progress).
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { ChatFileUpload } from '@/components/Chat/ChatFileUpload';
import HomeContext from '@/pages/api/home/home.context';

jest.mock('react-hot-toast', () => ({
  __esModule: true,
  default: { error: jest.fn(), success: jest.fn() },
}));

jest.mock('@aiqtoolkit-ui/common', () => ({
  UploadFilesDialog: ({ open }: { open?: boolean }) =>
    open ? <div data-testid="upload-files-dialog" /> : null,
  copyToClipboard: jest.fn(async () => true),
  uploadFileChunked: jest.fn(),
}));

const homeContextValue = {
  state: {
    agentApiUrlBase: 'https://agent.example.com/api/v1',
    chatUploadFileConfigTemplateJson: null,
    chatUploadFileMetadataEnabled: false,
    chatUploadFileHiddenMessageTemplate: '',
  },
  dispatch: jest.fn(),
};

function renderUploadHarness(disabled: boolean) {
  return render(
    <HomeContext.Provider value={homeContextValue as any}>
      <ChatFileUpload uploadFlowSourceId="test" disabled={disabled}>
        {({ isDragging, dragHandlers, triggerUpload }) => (
          <div>
            <span data-testid="is-dragging">{String(isDragging)}</span>
            <div data-testid="drop-zone" {...dragHandlers}>
              drop zone
            </div>
            <button type="button" onClick={triggerUpload}>
              trigger upload
            </button>
          </div>
        )}
      </ChatFileUpload>
    </HomeContext.Provider>,
  );
}

function dragEnterWithFiles(target: HTMLElement) {
  fireEvent.dragEnter(target, {
    dataTransfer: { items: [{ kind: 'file' }] },
  });
}

describe('ChatFileUpload – disabled during query', () => {
  it('does not show drag state on dragEnter when disabled', () => {
    renderUploadHarness(true);
    dragEnterWithFiles(screen.getByTestId('drop-zone'));
    expect(screen.getByTestId('is-dragging')).toHaveTextContent('false');
  });

  it('shows drag state on dragEnter when not disabled', () => {
    renderUploadHarness(false);
    dragEnterWithFiles(screen.getByTestId('drop-zone'));
    expect(screen.getByTestId('is-dragging')).toHaveTextContent('true');
  });

  it('does not open the upload dialog when triggerUpload is clicked while disabled', () => {
    renderUploadHarness(true);
    fireEvent.click(screen.getByRole('button', { name: 'trigger upload' }));
    expect(screen.queryByTestId('upload-files-dialog')).toBeNull();
  });

  it('opens the upload dialog when triggerUpload is clicked while enabled', () => {
    renderUploadHarness(false);
    fireEvent.click(screen.getByRole('button', { name: 'trigger upload' }));
    expect(screen.getByTestId('upload-files-dialog')).toBeTruthy();
  });
});

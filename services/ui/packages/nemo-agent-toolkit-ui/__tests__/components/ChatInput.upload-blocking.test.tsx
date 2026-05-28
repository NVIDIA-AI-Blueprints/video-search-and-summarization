/**
 * Tests that the chat upload button in ChatInput is disabled while a query
 * is in progress (loading or streaming).
 */

import React from 'react';
import { render, fireEvent } from '@testing-library/react';

jest.mock('next-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'en', changeLanguage: jest.fn() },
  }),
}));

jest.mock(
  require.resolve('../../lib-src/contexts/RuntimeConfigContext'),
  () => ({
    useWorkflowName: () => 'test-workflow',
    useRuntimeConfig: () => ({}),
    getStorageKey: (base: string) => base,
  }),
);

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

function createContextValue(stateOverrides: Record<string, unknown> = {}) {
  return {
    state: {
      selectedConversation: {
        id: 'c1',
        name: 'Test',
        messages: [],
        folderId: null,
      },
      messageIsStreaming: false,
      loading: false,
      webSocketMode: { current: false },
      customAgentParamsJson: null,
      chatUploadFileEnabled: true,
      chatInputMicEnabled: false,
      agentApiUrlBase: 'https://agent.example.com/api/v1',
      chatUploadFileConfigTemplateJson: null,
      chatUploadFileMetadataEnabled: false,
      chatUploadFileHiddenMessageTemplate: '',
      ...stateOverrides,
    },
    dispatch: jest.fn(),
  };
}

const mockContext = (() => {
  const React = require('react');
  return React.createContext(createContextValue());
})();

jest.mock('@/pages/api/home/home.context', () => ({
  __esModule: true,
  default: mockContext,
}));

function getUploadButton(container: HTMLElement): HTMLButtonElement | null {
  const leftBar = container.querySelector('.absolute.left-2');
  return leftBar?.querySelector('button') ?? null;
}

function renderChatInput(
  contextOverrides: Record<string, unknown> = {},
  props: Record<string, unknown> = {},
) {
  const { ChatInput } = require('@/components/Chat/ChatInput');
  const value = createContextValue(contextOverrides);

  return render(
    <mockContext.Provider value={value as any}>
      <ChatInput
        textareaRef={React.createRef<HTMLTextAreaElement>()}
        onSend={jest.fn()}
        onRegenerate={jest.fn()}
        onScrollDownClick={jest.fn()}
        showScrollDownButton={false}
        controller={{ current: new AbortController() }}
        onStopConversation={jest.fn()}
        {...props}
      />
    </mockContext.Provider>,
  );
}

describe('ChatInput – upload blocked during query', () => {
  it('disables the upload button while messageIsStreaming', () => {
    const { container } = renderChatInput({ messageIsStreaming: true });
    expect(getUploadButton(container)).toBeDisabled();
  });

  it('disables the upload button while loading', () => {
    const { container } = renderChatInput({ loading: true });
    expect(getUploadButton(container)).toBeDisabled();
  });

  it('enables the upload button when not processing', () => {
    const { container } = renderChatInput();
    expect(getUploadButton(container)).not.toBeDisabled();
  });

  it('disables the upload button when chatBlocked (upload flow active)', () => {
    const { container } = renderChatInput({}, { chatBlocked: true });
    expect(getUploadButton(container)).toBeDisabled();
  });

  it('does not open the upload dialog when the upload button is clicked during streaming', () => {
    const { container } = renderChatInput({ messageIsStreaming: true });
    const uploadButton = getUploadButton(container);
    expect(uploadButton).toBeTruthy();
    fireEvent.click(uploadButton!);
    expect(container.ownerDocument.querySelector('[data-testid="upload-files-dialog"]')).toBeNull();
  });

  it('opens the upload dialog when the upload button is clicked while idle', () => {
    const { container } = renderChatInput();
    const uploadButton = getUploadButton(container);
    expect(uploadButton).toBeTruthy();
    fireEvent.click(uploadButton!);
    expect(
      container.ownerDocument.querySelector('[data-testid="upload-files-dialog"]'),
    ).toBeTruthy();
  });
});

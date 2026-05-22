/**
 * Tests that the Agent Parameters control in ChatInput is blocked while a query
 * is in progress (loading or streaming).
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';

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

const CUSTOM_AGENT_PARAMS_JSON = JSON.stringify({
  params: [
    {
      name: 'mode',
      label: 'Mode',
      type: 'string',
      'default-value': 'fast',
    },
  ],
});

function createContextValue(overrides: Record<string, unknown> = {}) {
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
      customAgentParamsJson: CUSTOM_AGENT_PARAMS_JSON,
      chatUploadFileEnabled: false,
      chatInputMicEnabled: false,
      ...overrides,
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

function renderChatInput(contextOverrides: Record<string, unknown> = {}) {
  const { ChatInput } = require('@/components/Chat/ChatInput');
  const textareaRef = React.createRef<HTMLTextAreaElement>();
  const controllerRef = { current: new AbortController() };
  const value = createContextValue(contextOverrides);

  return render(
    <mockContext.Provider value={value as any}>
      <ChatInput
        textareaRef={textareaRef}
        onSend={jest.fn()}
        onRegenerate={jest.fn()}
        onScrollDownClick={jest.fn()}
        showScrollDownButton={false}
        controller={controllerRef}
        onStopConversation={jest.fn()}
      />
    </mockContext.Provider>,
  );
}

describe('ChatInput – custom agent params during query', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('disables Agent Parameters button while messageIsStreaming', () => {
    renderChatInput({ messageIsStreaming: true });
    expect(screen.getByTitle('Agent Parameters')).toBeDisabled();
  });

  it('disables Agent Parameters button while loading', () => {
    renderChatInput({ loading: true });
    expect(screen.getByTitle('Agent Parameters')).toBeDisabled();
  });

  it('enables Agent Parameters button when not processing', () => {
    renderChatInput();
    expect(screen.getByTitle('Agent Parameters')).not.toBeDisabled();
  });

  it('does not open the params dialog when clicked during streaming', () => {
    renderChatInput({ messageIsStreaming: true });
    fireEvent.click(screen.getByTitle('Agent Parameters'));
    expect(screen.queryByRole('dialog', { name: 'Agent parameters' })).toBeNull();
  });

  it('opens the params dialog when clicked while idle', () => {
    renderChatInput();
    fireEvent.click(screen.getByTitle('Agent Parameters'));
    expect(screen.getByRole('dialog', { name: 'Agent parameters' })).toBeTruthy();
  });

  it('closes the params dialog when streaming starts', () => {
    const { ChatInput } = require('@/components/Chat/ChatInput');
    const value = createContextValue();
    const { rerender } = render(
      <mockContext.Provider value={value as any}>
        <ChatInput
          textareaRef={React.createRef()}
          onSend={jest.fn()}
          onRegenerate={jest.fn()}
          onScrollDownClick={jest.fn()}
          showScrollDownButton={false}
          controller={{ current: new AbortController() }}
          onStopConversation={jest.fn()}
        />
      </mockContext.Provider>,
    );

    fireEvent.click(screen.getByTitle('Agent Parameters'));
    expect(screen.getByRole('dialog', { name: 'Agent parameters' })).toBeTruthy();

    rerender(
      <mockContext.Provider
        value={createContextValue({ messageIsStreaming: true }) as any}
      >
        <ChatInput
          textareaRef={React.createRef()}
          onSend={jest.fn()}
          onRegenerate={jest.fn()}
          onScrollDownClick={jest.fn()}
          showScrollDownButton={false}
          controller={{ current: new AbortController() }}
          onStopConversation={jest.fn()}
        />
      </mockContext.Provider>,
    );

    expect(screen.queryByRole('dialog', { name: 'Agent parameters' })).toBeNull();
  });
});

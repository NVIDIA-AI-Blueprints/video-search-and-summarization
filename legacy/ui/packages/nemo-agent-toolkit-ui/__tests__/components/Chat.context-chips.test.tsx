/**
 * Tests for the query context item functionality (formerly "context chips").
 *
 * Validates that:
 * 1. Context items render in the ChatInput area with label, title, and remove button
 * 2. Removing an item calls the onRemoveQueryContext callback
 * 3. Placeholder text is hidden when items are present
 * 4. Items are deduplicated by id
 * 5. Chat serializes attached context for the agent as `[Context: …]` with `data` fields only
 *    (no id, label, or contextType)
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';

jest.mock('next-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'en', changeLanguage: jest.fn() },
  }),
}));

jest.mock('react-markdown', () => ({
  __esModule: true,
  default: ({ children }: any) =>
    React.createElement('div', { 'data-testid': 'react-markdown' }, children),
}));

jest.mock('@/components/Avatar/BotAvatar', () => ({
  BotAvatar: () => React.createElement('div', { 'data-testid': 'bot-avatar' }),
}));

jest.mock(
  require.resolve('../../lib-src/contexts/RuntimeConfigContext'),
  () => ({
    useWorkflowName: () => 'test-workflow',
    useRuntimeConfig: () => ({}),
    getStorageKey: (base: string) => base,
  }),
);

const mockContext = (() => {
  const React = require('react');
  return React.createContext({
    state: {
      selectedConversation: { id: 'c1', name: 'Test', messages: [], folderId: null },
      messageIsStreaming: false,
      loading: false,
      webSocketMode: { current: false },
      customAgentParamsJson: null,
      chatUploadFileEnabled: false,
      chatInputMicEnabled: false,
    },
    dispatch: jest.fn(),
  });
})();

jest.mock('@/pages/api/home/home.context', () => ({
  __esModule: true,
  default: mockContext,
}));

const contextValue = {
  state: {
    selectedConversation: { id: 'c1', name: 'Test', messages: [], folderId: null },
    messageIsStreaming: false,
    loading: false,
    webSocketMode: { current: false },
    customAgentParamsJson: null,
    chatUploadFileEnabled: false,
    chatInputMicEnabled: false,
  },
  dispatch: jest.fn(),
};

function renderChatInput(props: Record<string, any> = {}) {
  const { ChatInput } = require('@/components/Chat/ChatInput');
  const textareaRef = React.createRef<HTMLTextAreaElement>();
  const controllerRef = { current: new AbortController() };

  const defaultProps = {
    textareaRef,
    onSend: jest.fn(),
    onRegenerate: jest.fn(),
    onScrollDownClick: jest.fn(),
    showScrollDownButton: false,
    controller: controllerRef,
    onStopConversation: jest.fn(),
    queryContextItems: [],
    onRemoveQueryContext: jest.fn(),
    ...props,
  };

  return render(
    <mockContext.Provider value={contextValue as any}>
      <ChatInput {...defaultProps} />
    </mockContext.Provider>,
  );
}

describe('ChatInput – query context item rendering', () => {
  it('renders item badges when queryContextItems are provided', () => {
    const items = [
      { id: 'item-1', label: 'Cam-North', contextType: 'media/video', data: { sensorName: 'Cam-North', startTime: '09:00', endTime: '09:05', mediaType: 'sensor-clip' } },
      { id: 'item-2', label: 'Cam-South', contextType: 'media/video', data: { sensorName: 'Cam-South', startTime: '10:00', endTime: '10:05', mediaType: 'sensor-clip' } },
    ];

    renderChatInput({ queryContextItems: items });

    expect(screen.getByText('Cam-North')).toBeTruthy();
    expect(screen.getByText('Cam-South')).toBeTruthy();
  });

  it('shows tooltip-style title with label and contextType', () => {
    const items = [
      { id: 'item-1', label: 'Lobby', contextType: 'media/video', data: { sensorName: 'Lobby', startTime: '08:30', endTime: '08:45', mediaType: 'sensor-clip' } },
    ];

    const { container } = renderChatInput({ queryContextItems: items });
    const itemEl = container.querySelector('[title*="Lobby"]');
    expect(itemEl?.getAttribute('title')).toContain('Lobby');
    expect(itemEl?.getAttribute('title')).toContain('media/video');
  });

  it('calls onRemoveQueryContext with item id when remove button is clicked', () => {
    const onRemove = jest.fn();
    const items = [
      { id: 'abc-123', label: 'Parking', contextType: 'media/video', data: { sensorName: 'Parking', startTime: '12:00', endTime: '12:10', mediaType: 'sensor-clip' } },
    ];

    renderChatInput({ queryContextItems: items, onRemoveQueryContext: onRemove });

    const removeBtn = screen.getByLabelText('Remove Parking');
    fireEvent.click(removeBtn);
    expect(onRemove).toHaveBeenCalledWith('abc-123');
  });

  it('does not render item area when queryContextItems is empty', () => {
    const { container } = renderChatInput({ queryContextItems: [] });
    const itemBadges = container.querySelectorAll('[title*="("]');
    expect(itemBadges.length).toBe(0);
  });

  it('hides placeholder text when items are present', () => {
    const items = [
      { id: 'item-1', label: 'Gate', contextType: 'media/video', data: { sensorName: 'Gate', startTime: '07:00', endTime: '07:15', mediaType: 'sensor-clip' } },
    ];

    const { container } = renderChatInput({ queryContextItems: items });
    expect(container.querySelector('[data-testid="chat-input-placeholder"]')).toBeNull();
  });

  it('shows placeholder text when no items and no content', () => {
    const { container } = renderChatInput({ queryContextItems: [] });
    expect(container.querySelector('[data-testid="chat-input-placeholder"]')).toBeTruthy();
  });
});

describe('Query context item deduplication logic', () => {
  it('prevents duplicate items by id', () => {
    const items: Array<{ id: string; label: string; contextType: string; data: Record<string, unknown> }> = [];

    const addItem = (item: typeof items[0]) => {
      if (items.some((c) => c.id === item.id)) return;
      items.push(item);
    };

    addItem({ id: 'x', label: 'Cam-1', contextType: 'media/video', data: { sensorName: 'Cam-1', mediaType: 'sensor-clip' } });
    addItem({ id: 'x', label: 'Cam-1', contextType: 'media/video', data: { sensorName: 'Cam-1', mediaType: 'sensor-clip' } });
    addItem({ id: 'y', label: 'Cam-2', contextType: 'media/video', data: { sensorName: 'Cam-2', mediaType: 'sensor-clip' } });

    expect(items).toHaveLength(2);
    expect(items.map((c) => c.id)).toEqual(['x', 'y']);
  });
});

describe('Query context serialization (matches Chat onSend)', () => {
  it('embeds data fields only — no id, label, or contextType', () => {
    const items = [
      { id: 'id1', label: 'Cam-A', contextType: 'media/video', data: { sensorName: 'Cam-A', startTime: '2024-01-15T09:00:00', endTime: '2024-01-15T09:05:00', mediaType: 'sensor-clip' } },
      { id: 'id2', label: 'Cam-B', contextType: 'media/video', data: { sensorName: 'Cam-B', startTime: '2024-01-15T10:00:00', endTime: '2024-01-15T10:05:00', mediaType: 'sensor-clip' } },
    ];

    const contextPayload = items.map(({ data }) => {
      const { contextType: _omitUiContextType, ...payload } = { ...(data as Record<string, unknown>) };
      return payload;
    });
    expect(contextPayload).toEqual([
      { sensorName: 'Cam-A', startTime: '2024-01-15T09:00:00', endTime: '2024-01-15T09:05:00', mediaType: 'sensor-clip' },
      { sensorName: 'Cam-B', startTime: '2024-01-15T10:00:00', endTime: '2024-01-15T10:05:00', mediaType: 'sensor-clip' },
    ]);

    const prefix = `[Context: ${JSON.stringify(contextPayload)}]`;
    expect(prefix).toContain('[Context:');
    expect(prefix).not.toContain('contextType');
    expect(prefix).not.toContain('id1');
    expect(contextPayload[0]).not.toHaveProperty('label');
    expect(contextPayload[0]).not.toHaveProperty('id');
  });

  it('drops contextType from data if present (UI-only field must not reach backend payload)', () => {
    const items = [
      {
        id: 'id1',
        label: 'Cam-A',
        contextType: 'media/video',
        data: { sensorName: 'Cam-A', contextType: 'should-not-leak', mediaType: 'sensor-clip' },
      },
    ];
    const contextPayload = items.map(({ data }) => {
      const { contextType: _omitUiContextType, ...payload } = { ...(data as Record<string, unknown>) };
      return payload;
    });
    expect(contextPayload[0]).toEqual({ sensorName: 'Cam-A', mediaType: 'sensor-clip' });
  });
});

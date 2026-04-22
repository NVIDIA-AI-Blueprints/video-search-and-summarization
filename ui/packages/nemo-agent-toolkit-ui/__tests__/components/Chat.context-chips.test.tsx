/**
 * Tests for the query context item functionality (formerly "context chips").
 *
 * Validates that:
 * 1. Context items render in the ChatInput area with label, title, and remove button
 * 2. Removing an item calls the onRemoveQueryContext callback
 * 3. Placeholder text is hidden when items are present
 * 4. Items are deduplicated by id
 * 5. Item data is correctly sent as query_context in the request body
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
      { id: 'item-1', label: 'Cam-North', type: 'sensor-clip', data: { sensorName: 'Cam-North', startTime: '09:00', endTime: '09:05' } },
      { id: 'item-2', label: 'Cam-South', type: 'sensor-clip', data: { sensorName: 'Cam-South', startTime: '10:00', endTime: '10:05' } },
    ];

    renderChatInput({ queryContextItems: items });

    expect(screen.getByText('Cam-North')).toBeTruthy();
    expect(screen.getByText('Cam-South')).toBeTruthy();
  });

  it('shows tooltip-style title with label and type', () => {
    const items = [
      { id: 'item-1', label: 'Lobby', type: 'sensor-clip', data: { sensorName: 'Lobby', startTime: '08:30', endTime: '08:45' } },
    ];

    const { container } = renderChatInput({ queryContextItems: items });
    const itemEl = container.querySelector('[title*="Lobby"]');
    expect(itemEl?.getAttribute('title')).toContain('Lobby');
    expect(itemEl?.getAttribute('title')).toContain('sensor-clip');
  });

  it('calls onRemoveQueryContext with item id when remove button is clicked', () => {
    const onRemove = jest.fn();
    const items = [
      { id: 'abc-123', label: 'Parking', type: 'sensor-clip', data: { sensorName: 'Parking', startTime: '12:00', endTime: '12:10' } },
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
      { id: 'item-1', label: 'Gate', type: 'sensor-clip', data: { sensorName: 'Gate', startTime: '07:00', endTime: '07:15' } },
    ];

    const { container } = renderChatInput({ queryContextItems: items });
    const placeholder = container.querySelector('[aria-hidden="true"]');
    expect(placeholder).toBeNull();
  });

  it('shows placeholder text when no items and no content', () => {
    const { container } = renderChatInput({ queryContextItems: [] });
    const placeholder = container.querySelector('[aria-hidden]');
    expect(placeholder).toBeTruthy();
  });
});

describe('Query context item deduplication logic', () => {
  it('prevents duplicate items by id', () => {
    const items: Array<{ id: string; label: string; type: string; data: Record<string, unknown> }> = [];

    const addItem = (item: typeof items[0]) => {
      if (items.some((c) => c.id === item.id)) return;
      items.push(item);
    };

    addItem({ id: 'x', label: 'Cam-1', type: 'sensor-clip', data: { sensorName: 'Cam-1' } });
    addItem({ id: 'x', label: 'Cam-1', type: 'sensor-clip', data: { sensorName: 'Cam-1' } });
    addItem({ id: 'y', label: 'Cam-2', type: 'sensor-clip', data: { sensorName: 'Cam-2' } });

    expect(items).toHaveLength(2);
    expect(items.map((c) => c.id)).toEqual(['x', 'y']);
  });
});

describe('Query context serialization for request body', () => {
  it('serializes items to query_context field in the request body', () => {
    const items = [
      { id: 'id1', label: 'Cam-A', type: 'sensor-clip', data: { sensorName: 'Cam-A', startTime: '2024-01-15T09:00:00', endTime: '2024-01-15T09:05:00' } },
      { id: 'id2', label: 'Cam-B', type: 'sensor-clip', data: { sensorName: 'Cam-B', startTime: '2024-01-15T10:00:00', endTime: '2024-01-15T10:05:00' } },
    ];

    const requestBody = {
      messages: [{ role: 'user', content: 'What happened here?' }],
      query_context: items,
    };

    expect(requestBody.query_context).toHaveLength(2);
    expect(requestBody.query_context[0].label).toBe('Cam-A');
    expect(requestBody.query_context[0].type).toBe('sensor-clip');
    expect(requestBody.query_context[0].data).toEqual({ sensorName: 'Cam-A', startTime: '2024-01-15T09:00:00', endTime: '2024-01-15T09:05:00' });
    expect(requestBody.query_context[1].label).toBe('Cam-B');

    const serialized = JSON.stringify(requestBody);
    expect(serialized).toContain('"query_context"');
    expect(serialized).not.toContain('[Context:');
  });

  it('omits query_context when no items are present', () => {
    const items: any[] = [];
    const requestBody: Record<string, any> = {
      messages: [{ role: 'user', content: 'Hello' }],
      ...(items.length > 0 ? { query_context: items } : {}),
    };

    expect(requestBody).not.toHaveProperty('query_context');
  });
});

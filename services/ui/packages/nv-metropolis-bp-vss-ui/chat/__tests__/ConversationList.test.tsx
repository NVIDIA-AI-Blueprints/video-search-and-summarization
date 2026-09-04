// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';

import { ConversationList } from '../lib-src/ConversationList';
import type { ChatSidebarControlHandlers } from '../lib-src/types';

const conversation = {
  id: 'conversation-1',
  name: 'Loading dock',
  messages: [],
  createdAt: 1,
};

function handlers(
  overrides: Partial<ChatSidebarControlHandlers> = {},
): ChatSidebarControlHandlers {
  return {
    conversations: [conversation],
    filteredConversations: [conversation],
    selectedConversationId: conversation.id,
    searchTerm: '',
    onSearchTermChange: jest.fn(),
    onSelectConversation: jest.fn(),
    onNewConversation: jest.fn(),
    onRenameConversation: jest.fn(),
    onDeleteConversation: jest.fn(),
    onClearConversations: jest.fn(),
    onExportData: jest.fn(),
    onImportConversations: jest.fn(),
    busy: false,
    ...overrides,
  };
}

describe('ConversationList', () => {
  it('keeps every primary control legible in light mode with dark-mode overrides', () => {
    render(<ConversationList {...handlers()} />);

    for (const control of [
      screen.getByRole('button', { name: 'New chat' }),
      screen.getByRole('button', { name: 'Loading dock' }),
      screen.getByRole('button', { name: 'Clear conversations' }),
      screen.getByRole('button', { name: 'Export data' }),
      screen.getByRole('button', { name: 'Import data' }),
    ]) {
      expect(control).toHaveClass('text-neutral-900', 'dark:text-white');
    }
    expect(screen.getByRole('textbox', { name: 'Search conversations' })).toHaveClass(
      'text-neutral-900',
      'dark:text-white',
    );
  });

  it('runs new, search, select, export, rename, delete, and clear controls', () => {
    const props = handlers();
    render(<ConversationList {...props} />);

    fireEvent.click(screen.getByRole('button', { name: 'New chat' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Search conversations' }), {
      target: { value: 'dock' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Loading dock' }));
    fireEvent.click(screen.getByRole('button', { name: 'Export data' }));

    fireEvent.click(screen.getByRole('button', { name: 'Rename Loading dock' }));
    const rename = screen.getByRole('textbox', { name: 'Conversation name' });
    fireEvent.change(rename, { target: { value: 'Warehouse' } });
    fireEvent.keyDown(rename, { key: 'Enter' });

    fireEvent.click(screen.getByRole('button', { name: 'Delete Loading dock' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm delete' }));
    fireEvent.click(screen.getByRole('button', { name: 'Clear conversations' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm clear' }));

    expect(props.onNewConversation).toHaveBeenCalledTimes(1);
    expect(props.onSearchTermChange).toHaveBeenCalledWith('dock');
    expect(props.onSelectConversation).toHaveBeenCalledWith(conversation.id);
    expect(props.onExportData).toHaveBeenCalledTimes(1);
    expect(props.onRenameConversation).toHaveBeenCalledWith(conversation.id, 'Warehouse');
    expect(props.onDeleteConversation).toHaveBeenCalledWith(conversation.id);
    expect(props.onClearConversations).toHaveBeenCalledTimes(1);
  });

  it('blocks conversation-changing controls while an answer is active', () => {
    render(<ConversationList {...handlers({ busy: true })} />);

    expect(screen.getByRole('button', { name: 'New chat' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Clear conversations' })).toBeDisabled();
  });
});

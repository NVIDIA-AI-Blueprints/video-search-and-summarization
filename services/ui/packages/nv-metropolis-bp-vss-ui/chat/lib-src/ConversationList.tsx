// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT AND Apache-2.0
/**
 * Conversation controls, rendered into whichever container the host app gives
 * them.
 *
 * The toolkit's equivalent (`ChatSidebarContent`) needed two React contexts
 * passed through props to stay reactive, because the list lived inside the
 * toolkit's own state tree. Here the panel hands over plain handlers via
 * `onControlsReady`, so this is a presentational component the host can render
 * anywhere — its left sidebar, a drawer, or not at all.
 */
import {
  IconCheck,
  IconDownload,
  IconMessage,
  IconPencil,
  IconPlus,
  IconSearch,
  IconTrash,
  IconUpload,
  IconX,
} from '@tabler/icons-react';
import React, { useRef, useState } from 'react';

import type { ChatSidebarControlHandlers } from './types';

export const ConversationList: React.FC<ChatSidebarControlHandlers> = ({
  filteredConversations,
  selectedConversationId,
  searchTerm,
  onSearchTermChange,
  onSelectConversation,
  onNewConversation,
  onRenameConversation,
  onDeleteConversation,
  onClearConversations,
  onExportData,
  onImportConversations,
  busy,
}) => {
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState('');
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null);
  const [confirmingClear, setConfirmingClear] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const commitRename = () => {
    if (renamingId) onRenameConversation(renamingId, draftName);
    setRenamingId(null);
  };

  return (
    <div className="flex h-full flex-col gap-2 p-2 text-sm">
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onNewConversation}
          disabled={busy}
          title={busy ? 'Wait for the current answer to finish' : 'New chat'}
          className="flex flex-1 items-center gap-2 rounded-md border border-black/20 p-2 text-neutral-900 transition-colors hover:bg-gray-500/10 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/20 dark:text-white"
        >
          <IconPlus size={16} /> New chat
        </button>
      </div>

      <div className="relative">
        <IconSearch
          size={16}
          className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-neutral-400"
        />
        <input
          type="text"
          className="w-full rounded-md border border-neutral-400 bg-transparent py-2 pl-8 pr-8 text-neutral-900 outline-none focus:ring-1 focus:ring-[#76b900] dark:border-neutral-600 dark:text-white"
          placeholder="Search conversations…"
          value={searchTerm}
          onChange={(e) => onSearchTermChange(e.target.value)}
          aria-label="Search conversations"
        />
        {searchTerm && (
          <button
            type="button"
            onClick={() => onSearchTermChange('')}
            aria-label="Clear search"
            className="absolute right-2 top-1/2 -translate-y-1/2 text-neutral-500 hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-white"
          >
            <IconX size={16} />
          </button>
        )}
      </div>

      <ul className="flex-1 overflow-y-auto">
        {filteredConversations.length === 0 ? (
          <li className="p-2 text-neutral-500 dark:text-neutral-400">No conversations</li>
        ) : (
          filteredConversations.map((conversation) => {
            const active = conversation.id === selectedConversationId;
            return (
              <li key={conversation.id}>
                <div
                  className={`group flex items-center gap-2 rounded-md p-2 ${
                    active ? 'bg-[#76b900]/20' : 'hover:bg-gray-500/10'
                  }`}
                >
                  {renamingId === conversation.id ? (
                    <input
                      autoFocus
                      className="min-w-0 flex-1 bg-transparent text-neutral-900 outline-none dark:text-white"
                      value={draftName}
                      onChange={(e) => setDraftName(e.target.value)}
                      onBlur={commitRename}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') commitRename();
                        if (e.key === 'Escape') setRenamingId(null);
                      }}
                      aria-label="Conversation name"
                    />
                  ) : (
                    <button
                      type="button"
                      onClick={() => onSelectConversation(conversation.id)}
                      onDoubleClick={() => {
                        setRenamingId(conversation.id);
                        setDraftName(conversation.name);
                      }}
                      className="flex min-w-0 flex-1 items-center gap-2 text-left text-neutral-900 dark:text-white"
                      title={conversation.name}
                    >
                      <IconMessage size={16} className="flex-shrink-0" />
                      <span className="truncate">{conversation.name}</span>
                    </button>
                  )}

                  {confirmingDeleteId !== conversation.id && renamingId !== conversation.id && (
                    <button
                      type="button"
                      aria-label={`Rename ${conversation.name}`}
                      onClick={() => {
                        setRenamingId(conversation.id);
                        setDraftName(conversation.name);
                      }}
                      className="flex-shrink-0 text-neutral-500 opacity-0 transition-opacity hover:text-neutral-900 group-hover:opacity-100 dark:text-neutral-400 dark:hover:text-white"
                    >
                      <IconPencil size={16} />
                    </button>
                  )}

                  {confirmingDeleteId === conversation.id ? (
                    <span className="flex flex-shrink-0 gap-1">
                      <button
                        type="button"
                        aria-label="Confirm delete"
                        onClick={() => {
                          onDeleteConversation(conversation.id);
                          setConfirmingDeleteId(null);
                        }}
                        className="text-neutral-600 hover:text-[#76b900] dark:text-neutral-300"
                      >
                        <IconCheck size={16} />
                      </button>
                      <button
                        type="button"
                        aria-label="Cancel delete"
                        onClick={() => setConfirmingDeleteId(null)}
                        className="text-neutral-600 hover:text-neutral-900 dark:text-neutral-300 dark:hover:text-white"
                      >
                        <IconX size={16} />
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      aria-label={`Delete ${conversation.name}`}
                      onClick={() => setConfirmingDeleteId(conversation.id)}
                      className="flex-shrink-0 text-neutral-500 opacity-0 transition-opacity hover:text-red-600 group-hover:opacity-100 dark:text-neutral-400 dark:hover:text-red-400"
                    >
                      <IconTrash size={16} />
                    </button>
                  )}
                </div>
              </li>
            );
          })
        )}
      </ul>

      <div className="flex flex-col gap-1 border-t border-black/10 pt-2 dark:border-white/10">
        {confirmingClear ? (
          <div className="flex items-center gap-2 p-2 text-neutral-900 dark:text-white">
            <span className="flex-1">Clear all conversations?</span>
            <button
              type="button"
              aria-label="Confirm clear"
              onClick={() => {
                onClearConversations();
                setConfirmingClear(false);
              }}
              className="hover:text-[#76b900]"
            >
              <IconCheck size={16} />
            </button>
            <button
              type="button"
              aria-label="Cancel clear"
              onClick={() => setConfirmingClear(false)}
              className="hover:text-neutral-900 dark:hover:text-white"
            >
              <IconX size={16} />
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setConfirmingClear(true)}
            disabled={busy}
            className="flex items-center gap-2 rounded-md p-2 text-neutral-900 hover:bg-gray-500/10 disabled:opacity-40 dark:text-white"
          >
            <IconTrash size={16} /> Clear conversations
          </button>
        )}

        <button
          type="button"
          onClick={onExportData}
          className="flex items-center gap-2 rounded-md p-2 text-neutral-900 hover:bg-gray-500/10 dark:text-white"
        >
          <IconDownload size={16} /> Export data
        </button>

        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          className="flex items-center gap-2 rounded-md p-2 text-neutral-900 hover:bg-gray-500/10 dark:text-white"
        >
          <IconUpload size={16} /> Import data
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".json"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            // Reset so re-importing the same file fires a change event.
            event.target.value = '';
            if (!file) return;
            const reader = new FileReader();
            reader.onload = (e) => onImportConversations(String(e.target?.result ?? ''));
            reader.readAsText(file);
          }}
        />
      </div>
    </div>
  );
};

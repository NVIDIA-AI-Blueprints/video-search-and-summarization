// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Conversation controls, rendered into whichever container the host app gives
 * them.
 *
 * The panel hands over plain handlers via `onControlsReady`, so this is a
 * presentational component the host can render anywhere — its left sidebar, a
 * drawer, or not at all.
 */
import {
  IconCaretDown,
  IconCaretRight,
  IconCheck,
  IconDownload,
  IconFolder,
  IconFolderPlus,
  IconMessage,
  IconPencil,
  IconPlus,
  IconSearch,
  IconTrash,
  IconUpload,
  IconX,
} from '@tabler/icons-react';
import React, { useMemo, useRef, useState } from 'react';

import type { ChatFolder, ChatSidebarControlHandlers, Conversation } from './types';

export const ConversationList: React.FC<ChatSidebarControlHandlers> = ({
  conversations,
  filteredConversations,
  folders,
  selectedConversationId,
  searchTerm,
  onSearchTermChange,
  onSelectConversation,
  onNewConversation,
  onRenameConversation,
  onDeleteConversation,
  onMoveConversation,
  onCreateFolder,
  onRenameFolder,
  onDeleteFolder,
  onClearConversations,
  onExportData,
  onImportConversations,
  busy,
}) => {
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState('');
  const [movingId, setMovingId] = useState<string | null>(null);
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null);
  const [renamingFolderId, setRenamingFolderId] = useState<string | null>(null);
  const [folderDraftName, setFolderDraftName] = useState('');
  const [confirmingDeleteFolderId, setConfirmingDeleteFolderId] = useState<string | null>(null);
  const [openFolderIds, setOpenFolderIds] = useState<string[]>([]);
  const [confirmingClear, setConfirmingClear] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const sortedFolders = useMemo(
    () => [...folders].sort((left, right) => left.name.localeCompare(right.name)),
    [folders],
  );
  const knownFolderIds = useMemo(() => new Set(folders.map((folder) => folder.id)), [folders]);
  const unfiledConversations = filteredConversations.filter(
    (conversation) => !conversation.folderId || !knownFolderIds.has(conversation.folderId),
  );

  const commitRename = () => {
    if (renamingId) onRenameConversation(renamingId, draftName);
    setRenamingId(null);
  };

  const commitFolderRename = () => {
    if (renamingFolderId) onRenameFolder(renamingFolderId, folderDraftName);
    setRenamingFolderId(null);
  };

  const toggleFolder = (id: string) => {
    setOpenFolderIds((current) =>
      current.includes(id) ? current.filter((folderId) => folderId !== id) : [...current, id],
    );
  };

  const moveDroppedConversation = (event: React.DragEvent, folderId: string | null) => {
    event.preventDefault();
    const conversationId = event.dataTransfer.getData('text/plain');
    if (conversationId) onMoveConversation(conversationId, folderId);
    if (folderId) {
      setOpenFolderIds((current) =>
        current.includes(folderId) ? current : [...current, folderId],
      );
    }
  };

  const renderConversation = (conversation: Conversation) => {
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
              onChange={(event) => setDraftName(event.target.value)}
              onBlur={commitRename}
              onKeyDown={(event) => {
                if (event.key === 'Enter') commitRename();
                if (event.key === 'Escape') setRenamingId(null);
              }}
              aria-label="Conversation name"
            />
          ) : (
            <button
              type="button"
              draggable={!busy}
              onDragStart={(event) => {
                event.dataTransfer.effectAllowed = 'move';
                event.dataTransfer.setData('text/plain', conversation.id);
              }}
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

          {movingId === conversation.id && (
            <select
              autoFocus
              aria-label={`Move ${conversation.name} to folder`}
              value={conversation.folderId ?? ''}
              onBlur={() => setMovingId(null)}
              onChange={(event) => {
                onMoveConversation(conversation.id, event.target.value || null);
                setMovingId(null);
              }}
              className="min-w-0 max-w-24 rounded border border-neutral-400 bg-white text-xs text-neutral-900 dark:border-neutral-600 dark:bg-neutral-900 dark:text-white"
            >
              <option value="">No folder</option>
              {sortedFolders.map((folder) => (
                <option key={folder.id} value={folder.id}>
                  {folder.name}
                </option>
              ))}
            </select>
          )}
          {movingId !== conversation.id &&
            sortedFolders.length > 0 &&
            renamingId !== conversation.id && (
              <button
                type="button"
                aria-label={`Move ${conversation.name}`}
                title="Move to folder"
                disabled={busy}
                onClick={() => setMovingId(conversation.id)}
                className="flex-shrink-0 text-neutral-500 opacity-0 transition-opacity hover:text-neutral-900 group-hover:opacity-100 focus:opacity-100 disabled:opacity-40 dark:text-neutral-400 dark:hover:text-white"
              >
                <IconFolder size={16} />
              </button>
            )}

          {confirmingDeleteId !== conversation.id && renamingId !== conversation.id ? (
            <button
              type="button"
              aria-label={`Rename ${conversation.name}`}
              disabled={busy}
              onClick={() => {
                setRenamingId(conversation.id);
                setDraftName(conversation.name);
              }}
              className="flex-shrink-0 text-neutral-500 opacity-0 transition-opacity hover:text-neutral-900 group-hover:opacity-100 focus:opacity-100 disabled:opacity-40 dark:text-neutral-400 dark:hover:text-white"
            >
              <IconPencil size={16} />
            </button>
          ) : null}

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
              disabled={busy}
              onClick={() => setConfirmingDeleteId(conversation.id)}
              className="flex-shrink-0 text-neutral-500 opacity-0 transition-opacity hover:text-red-600 group-hover:opacity-100 focus:opacity-100 disabled:opacity-40 dark:text-neutral-400 dark:hover:text-red-400"
            >
              <IconTrash size={16} />
            </button>
          )}
        </div>
      </li>
    );
  };

  const conversationsForFolder = (folder: ChatFolder) => {
    const folderMatches = folder.name.toLowerCase().includes(searchTerm.trim().toLowerCase());
    const source = folderMatches ? conversations : filteredConversations;
    return source.filter((conversation) => conversation.folderId === folder.id);
  };

  const hasVisibleFolder = sortedFolders.some((folder) => {
    if (!searchTerm) return true;
    if (folder.name.toLowerCase().includes(searchTerm.trim().toLowerCase())) return true;
    return conversationsForFolder(folder).length > 0;
  });

  return (
    <div className="flex h-full flex-col gap-2 p-2 text-sm">
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => onNewConversation()}
          disabled={busy}
          title={busy ? 'Wait for the current answer to finish' : 'New chat'}
          className="flex flex-1 items-center gap-2 rounded-md border border-black/20 p-2 text-neutral-900 transition-colors hover:bg-gray-500/10 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/20 dark:text-white"
        >
          <IconPlus size={16} /> New chat
        </button>
        <button
          type="button"
          onClick={() => {
            const folder = onCreateFolder();
            setOpenFolderIds((current) => [...current, folder.id]);
            setRenamingFolderId(folder.id);
            setFolderDraftName(folder.name);
          }}
          disabled={busy}
          title={busy ? 'Wait for the current answer to finish' : 'New folder'}
          aria-label="New folder"
          className="flex flex-1 items-center gap-2 rounded-md border border-black/20 p-2 text-neutral-900 transition-colors hover:bg-gray-500/10 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/20 dark:text-white"
        >
          <IconFolderPlus size={16} /> New folder
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
          onChange={(event) => onSearchTermChange(event.target.value)}
          aria-label="Search conversations"
        />
        {searchTerm ? (
          <button
            type="button"
            onClick={() => onSearchTermChange('')}
            aria-label="Clear search"
            className="absolute right-2 top-1/2 -translate-y-1/2 text-neutral-500 hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-white"
          >
            <IconX size={16} />
          </button>
        ) : null}
      </div>

      <div className="flex-1 overflow-y-auto">
        {unfiledConversations.length > 0 ? (
          <ul
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => moveDroppedConversation(event, null)}
          >
            {unfiledConversations.map(renderConversation)}
          </ul>
        ) : null}

        {sortedFolders.map((folder) => {
          const folderConversations = conversationsForFolder(folder);
          const folderMatches = folder.name.toLowerCase().includes(searchTerm.trim().toLowerCase());
          if (searchTerm && !folderMatches && folderConversations.length === 0) return null;
          const open = openFolderIds.includes(folder.id) || !!searchTerm;
          let folderActions: React.ReactNode = (
            <>
              <button
                type="button"
                aria-label={`Rename folder ${folder.name}`}
                disabled={busy}
                onClick={() => {
                  setRenamingFolderId(folder.id);
                  setFolderDraftName(folder.name);
                }}
                className="text-neutral-500 opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100 disabled:opacity-40 dark:text-neutral-400"
              >
                <IconPencil size={16} />
              </button>
              <button
                type="button"
                aria-label={`Delete folder ${folder.name}`}
                disabled={busy}
                onClick={() => setConfirmingDeleteFolderId(folder.id)}
                className="text-neutral-500 opacity-0 transition-opacity hover:text-red-600 group-hover:opacity-100 focus:opacity-100 disabled:opacity-40 dark:text-neutral-400 dark:hover:text-red-400"
              >
                <IconTrash size={16} />
              </button>
            </>
          );
          if (confirmingDeleteFolderId === folder.id) {
            folderActions = (
              <>
                <button
                  type="button"
                  aria-label={`Confirm delete folder ${folder.name}`}
                  onClick={() => {
                    onDeleteFolder(folder.id);
                    setConfirmingDeleteFolderId(null);
                  }}
                  className="text-neutral-600 hover:text-[#76b900] dark:text-neutral-300"
                >
                  <IconCheck size={16} />
                </button>
                <button
                  type="button"
                  aria-label="Cancel folder delete"
                  onClick={() => setConfirmingDeleteFolderId(null)}
                  className="text-neutral-600 hover:text-neutral-900 dark:text-neutral-300 dark:hover:text-white"
                >
                  <IconX size={16} />
                </button>
              </>
            );
          }
          if (renamingFolderId === folder.id) folderActions = null;

          return (
            <div key={folder.id} className="mt-1">
              <div className="group relative flex items-center rounded-md hover:bg-gray-500/10">
                {renamingFolderId === folder.id ? (
                  <div className="flex min-w-0 flex-1 items-center gap-2 p-2 pr-16">
                    {open ? <IconCaretDown size={16} /> : <IconCaretRight size={16} />}
                    <input
                      autoFocus
                      aria-label="Folder name"
                      value={folderDraftName}
                      onChange={(event) => setFolderDraftName(event.target.value)}
                      onBlur={commitFolderRename}
                      onDragOver={(event) => event.preventDefault()}
                      onDrop={(event) => moveDroppedConversation(event, folder.id)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') commitFolderRename();
                        if (event.key === 'Escape') setRenamingFolderId(null);
                      }}
                      className="min-w-0 flex-1 bg-transparent text-neutral-900 outline-none dark:text-white"
                    />
                  </div>
                ) : (
                  <button
                    type="button"
                    aria-expanded={open}
                    onClick={() => toggleFolder(folder.id)}
                    onDragOver={(event) => event.preventDefault()}
                    onDrop={(event) => moveDroppedConversation(event, folder.id)}
                    className="flex min-w-0 flex-1 items-center gap-2 p-2 pr-16 text-left text-neutral-900 dark:text-white"
                  >
                    {open ? <IconCaretDown size={16} /> : <IconCaretRight size={16} />}
                    <IconFolder size={16} />
                    <span className="truncate">{folder.name}</span>
                  </button>
                )}

                <span className="absolute right-1 flex items-center gap-1">
                  {folderActions}
                </span>
              </div>

              {open ? (
                <ul
                  className="ml-4 border-l border-black/10 pl-1 dark:border-white/10"
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={(event) => moveDroppedConversation(event, folder.id)}
                >
                  {folderConversations.length > 0 ? (
                    folderConversations.map(renderConversation)
                  ) : (
                    <li className="p-2 text-xs text-neutral-500 dark:text-neutral-400">
                      Drop conversations here
                    </li>
                  )}
                </ul>
              ) : null}
            </div>
          );
        })}

        {unfiledConversations.length === 0 && !hasVisibleFolder ? (
          <p className="p-2 text-neutral-500 dark:text-neutral-400">No conversations</p>
        ) : null}
      </div>

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
            event.target.value = '';
            if (!file) return;
            const reader = new FileReader();
            reader.onload = (loadEvent) => {
              const content = loadEvent.target?.result;
              if (typeof content === 'string') onImportConversations(content);
            };
            reader.readAsText(file);
          }}
        />
      </div>
    </div>
  );
};

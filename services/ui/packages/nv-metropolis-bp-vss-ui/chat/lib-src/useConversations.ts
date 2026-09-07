// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT AND Apache-2.0
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  buildExport,
  createConversation,
  exportFilename,
  filterConversations,
  mergeConversations,
  mergeExportAuxiliary,
  parseImport,
  sanitizeForPersistence,
  titleFromMessage,
} from './conversations';
import {
  clearAllConversations,
  initConversationSessionLifecycle,
  loadChatExportAuxiliary,
  loadConversations,
  loadSelectedConversationId,
  saveChatExportAuxiliary,
  saveConversations,
  saveSelectedConversationId,
} from './storage';
import type { ChatMessage, Conversation } from './types';

export interface UseConversationsResult {
  conversations: Conversation[];
  selected: Conversation | null;
  /** False until IndexedDB has been read, so the UI does not flash an empty thread. */
  hydrated: boolean;
  searchTerm: string;
  setSearchTerm: (term: string) => void;
  filtered: Conversation[];
  select: (id: string) => void;
  create: () => Conversation;
  rename: (id: string, name: string) => void;
  remove: (id: string) => void;
  clearAll: () => void;
  /** Replace the selected conversation's messages. */
  setMessages: (updater: (prev: ChatMessage[]) => ChatMessage[]) => void;
  /** Name an untitled conversation after its first user message. */
  titleIfUntitled: (content: string) => void;
  exportData: () => void;
  importData: (rawJson: string) => { ok: boolean; error?: string };
}

/**
 * Owns the conversation list and keeps it in IndexedDB.
 *
 * Writes are debounced: streaming replaces the assistant message on every
 * token, and persisting each one would put a transaction on the critical path
 * of the render loop.
 */
export function useConversations(storageKeyPrefix?: string): UseConversationsResult {
  // Start with a real conversation rather than an empty list. Reading
  // IndexedDB is async, and anything the user types in that window — a
  // keystroke, or an embedder calling submitChatMessage on mount — would
  // otherwise be written into a conversation that does not exist yet and
  // silently vanish.
  const [conversations, setConversations] = useState<Conversation[]>(() => [createConversation()]);
  const [selectedId, setSelectedId] = useState<string | null>(() => conversations[0]?.id ?? null);
  const [hydrated, setHydrated] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');

  const conversationsRef = useRef(conversations);
  conversationsRef.current = conversations;

  // Hydrate once, then never read again — this hook is the source of truth.
  useEffect(() => {
    let cancelled = false;
    initConversationSessionLifecycle();
    void (async () => {
      try {
        const [stored, storedId] = await Promise.all([
          loadConversations(storageKeyPrefix),
          loadSelectedConversationId(storageKeyPrefix),
        ]);
        if (cancelled || !stored.length) return;
        // Keep anything already typed into the placeholder conversation; a
        // slow disk should not discard the user's first message.
        setConversations((current) => {
          const started = current.filter((c) => c.messages.length > 0);
          return [...stored, ...started];
        });
        setSelectedId((current) => {
          const startedTyping = conversationsRef.current.some(
            (c) => c.id === current && c.messages.length > 0,
          );
          if (startedTyping) return current;
          return stored.some((c) => c.id === storedId) ? storedId : stored[0].id;
        });
      } catch (error) {
        console.warn('vss-chat: could not load conversations', error);
      } finally {
        if (!cancelled) setHydrated(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [storageKeyPrefix]);

  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!hydrated) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      saveConversations(sanitizeForPersistence(conversations), storageKeyPrefix).catch((error) =>
        console.warn('vss-chat: could not save conversations', error),
      );
    }, 400);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [conversations, hydrated, storageKeyPrefix]);

  useEffect(() => {
    if (!hydrated) return;
    saveSelectedConversationId(selectedId, storageKeyPrefix).catch(() => {});
  }, [selectedId, hydrated, storageKeyPrefix]);

  const selected = useMemo(
    () => conversations.find((c) => c.id === selectedId) ?? null,
    [conversations, selectedId],
  );

  const filtered = useMemo(
    () => filterConversations(conversations, searchTerm),
    [conversations, searchTerm],
  );

  const setMessages = useCallback(
    (updater: (prev: ChatMessage[]) => ChatMessage[]) => {
      setConversations((prev) =>
        prev.map((c) => (c.id === selectedId ? { ...c, messages: updater(c.messages) } : c)),
      );
    },
    [selectedId],
  );

  const titleIfUntitled = useCallback(
    (content: string) => {
      setConversations((prev) =>
        prev.map((c) =>
          // Only the first user turn names the thread; a rename must stick.
          c.id === selectedId && c.messages.filter((m) => !m.hidden).length <= 1
            ? { ...c, name: titleFromMessage(content) }
            : c,
        ),
      );
    },
    [selectedId],
  );

  const create = useCallback(() => {
    const fresh = createConversation();
    setConversations((prev) => [...prev, fresh]);
    setSelectedId(fresh.id);
    return fresh;
  }, []);

  const remove = useCallback((id: string) => {
    setConversations((prev) => {
      const next = prev.filter((c) => c.id !== id);
      if (next.length) {
        setSelectedId((cur) => (cur === id ? next[next.length - 1].id : cur));
        return next;
      }
      // Never leave the panel with no conversation to render into.
      const fresh = createConversation();
      setSelectedId(fresh.id);
      return [fresh];
    });
  }, []);

  const rename = useCallback((id: string, name: string) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, name: trimmed } : c)));
  }, []);

  const clearAll = useCallback(() => {
    const fresh = createConversation();
    setConversations([fresh]);
    setSelectedId(fresh.id);
    clearAllConversations(storageKeyPrefix).catch(() => {});
  }, [storageKeyPrefix]);

  const exportData = useCallback(() => {
    if (typeof window === 'undefined') return;
    const blob = new Blob([
      JSON.stringify(
        buildExport(conversations, loadChatExportAuxiliary(storageKeyPrefix)),
        null,
        2,
      ),
    ], {
      type: 'application/json',
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = exportFilename();
    link.click();
    URL.revokeObjectURL(url);
  }, [conversations, storageKeyPrefix]);

  const importData = useCallback((rawJson: string) => {
    const { conversations: imported, folders, prompts, error } = parseImport(rawJson);
    if (!imported) return { ok: false, error };
    const auxiliary = mergeExportAuxiliary(
      loadChatExportAuxiliary(storageKeyPrefix),
      { folders, prompts },
    );
    try {
      saveChatExportAuxiliary(auxiliary, storageKeyPrefix);
    } catch {
      return { ok: false, error: 'Failed to preserve imported folders and prompts' };
    }
    // Append rather than replace: an import should not destroy live threads.
    setConversations((prev) => mergeConversations(prev, imported));
    if (imported.length) setSelectedId(imported[imported.length - 1].id);
    return { ok: true };
  }, [storageKeyPrefix]);

  // Memoised: this object is a dependency of the `onControlsReady` handshake,
  // and a fresh identity every render would call the embedder's setState on
  // every render — an infinite loop rather than a slow one.
  return useMemo(
    () => ({
      conversations,
      selected,
      hydrated,
      searchTerm,
      setSearchTerm,
      filtered,
      select: setSelectedId,
      create,
      rename,
      remove,
      clearAll,
      setMessages,
      titleIfUntitled,
      exportData,
      importData,
    }),
    [
      conversations,
      selected,
      hydrated,
      searchTerm,
      filtered,
      create,
      rename,
      remove,
      clearAll,
      setMessages,
      titleIfUntitled,
      exportData,
      importData,
    ],
  );
}

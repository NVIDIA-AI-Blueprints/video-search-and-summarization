// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT AND Apache-2.0
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { ChatHeader } from './ChatHeader';
import { ChatInput } from './ChatInput';
import { ChatMessageView } from './ChatMessage';
import { createRandomId } from './id';
import type { InteractionRequest } from './sse';
import { useChatStream } from './useChatStream';
import { useConversations } from './useConversations';
import type {
  ChatFeatureFlags,
  ChatPanelProps,
  ChatSidebarControlHandlers,
  QueryDataContext,
} from './types';

/**
 * Defaults chosen to match what the VSS deployment actually sets in
 * `deploy/docker/resolved.yml`, so an unconfigured embed behaves like the
 * toolkit chat bar it replaces rather than like a bare component.
 */
const DEFAULT_FEATURES: Required<ChatFeatureFlags> = {
  chatHistory: true,
  intermediateSteps: true,
  expandIntermediateSteps: false,
  messageCopy: false,
  messageEdit: false,
  messageSpeaker: false,
  inputMic: false,
  uploadFile: true,
  uploadFileMetadata: false,
  themeToggle: false,
  headerMenu: true,
};

/** Stable per-mount id so the backend maps this panel to one agent thread. */
function useFallbackConversationId(supplied?: string): string {
  const ref = useRef(supplied);
  if (!ref.current) {
    ref.current = `vss-${createRandomId()}`;
  }
  return ref.current;
}

/**
 * VSS chat surface.
 *
 * Used for both the main chat tab and the docked sidebar; the only difference
 * is the container it is given. It uses the structured agent API transport when
 * configured and keeps the original chat-SSE contract as a fallback.
 */
export const ChatPanel: React.FC<ChatPanelProps> = ({
  endpoint,
  title,
  theme = 'dark',
  onThemeChange,
  placeholder,
  showSteps,
  features: featuresProp,
  customAgentParamsJson,
  uploadConfigTemplateJson,
  uploadHiddenMessageTemplate,
  storageKeyPrefix,
  isActive = true,
  onAnswer,
  onAnswerComplete,
  onSubmit,
  onSubmitMessageReady,
  onMessageSubmitted,
  onAddQueryContextReady,
  onChatVideoUploadComplete,
  onBusyChange,
  onControlsReady,
  className,
}) => {
  const features = useMemo<Required<ChatFeatureFlags>>(
    () => ({
      ...DEFAULT_FEATURES,
      // `showSteps` predates the flags object; honour it so existing embeds
      // that pass it keep working.
      ...(showSteps === undefined ? {} : { intermediateSteps: showSteps }),
      ...featuresProp,
    }),
    [featuresProp, showSteps],
  );

  const conversations = useConversations(storageKeyPrefix);
  const {
    selected,
    setMessages,
    titleIfUntitled,
    hydrated,
    create: createConversation,
  } = conversations;

  // The endpoint's conversation id is what the adapter keys its session on.
  // Following the selected conversation means switching threads in the UI also
  // switches the agent's memory, instead of leaking one into the other.
  const fallbackId = useFallbackConversationId(endpoint.conversationId);
  const conversationId = endpoint.conversationId ?? selected?.id ?? fallbackId;

  const [chatHistory, setChatHistory] = useState(features.chatHistory);
  const [contextItems, setContextItems] = useState<QueryDataContext[]>([]);
  const [uploadFlowActive, setUploadFlowActive] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [interaction, setInteraction] = useState<InteractionRequest | null>(null);
  const [interactionText, setInteractionText] = useState('');
  const interactionResolveRef = useRef<((value: string) => void) | null>(null);

  const messages = selected?.messages ?? [];
  const logRef = useRef<HTMLDivElement | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const selectedIdRef = useRef<string | undefined>(selected?.id);
  selectedIdRef.current = selected?.id;

  const config = useMemo(
    () => ({ ...endpoint, conversationId }),
    [endpoint, conversationId],
  );

  // The conversation goes with the answer: consumers fetch per-conversation
  // artifacts, and a process-wide 'last result' would cross conversations.
  const handleAnswer = useCallback(
    (answer: string) => onAnswer?.(answer, conversationId),
    [onAnswer, conversationId],
  );

  const isConversationStale = useCallback(
    (uploadConversationId: string) => selectedIdRef.current !== uploadConversationId,
    [],
  );

  const requestInteraction = useCallback(
    (request: InteractionRequest) =>
      new Promise<string>((resolve) => {
        setInteraction(request);
        setInteractionText('');
        interactionResolveRef.current = resolve;
      }),
    [],
  );

  const { busy, send, abort } = useChatStream(config, {
    messages,
    setMessages,
    chatHistory,
    onAnswer: handleAnswer,
    onAnswerComplete,
    onBusyChange,
    isConversationStale,
    onInteraction: requestInteraction,
  });

  const submitInteraction = useCallback(() => {
    if (!interaction || (interaction.prompt.required && !interactionText.trim())) return;
    const resolve = interactionResolveRef.current;
    interactionResolveRef.current = null;
    setInteraction(null);
    resolve?.(interactionText);
  }, [interaction, interactionText]);

  const stopTurn = useCallback(() => {
    interactionResolveRef.current?.('/cancel');
    interactionResolveRef.current = null;
    setInteraction(null);
    abort();
  }, [abort]);

  const notify = useCallback((message: string) => {
    setNotice(message);
    setTimeout(() => setNotice(null), 4000);
  }, []);

  // Auto-scroll unless the user has scrolled up to read something — pinning
  // them to the bottom mid-answer is the fastest way to make a long reply
  // unreadable.
  const handleScroll = useCallback(() => {
    const el = logRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    setAutoScroll(atBottom);
  }, []);

  useEffect(() => {
    if (!isActive || !autoScroll) return;
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, isActive, autoScroll]);

  const scrollDown = useCallback(() => {
    setAutoScroll(true);
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, []);

  const submitText = useCallback(
    (text: string, params?: Record<string, string | number | boolean>) => {
      const items = contextItems;
      if (items.length) setContextItems([]);
      titleIfUntitled(text);
      onSubmit?.(text);
      void send(text, { params, context: items });
    },
    [contextItems, onSubmit, send, titleIfUntitled],
  );

  // Programmatic submit for the Search / Alerts tabs. Registered once —
  // `send` is stable, so embedders are not re-registered on every token.
  const submitRef = useRef(submitText);
  submitRef.current = submitText;
  useEffect(() => {
    onSubmitMessageReady?.((message: string) => {
      submitRef.current(message);
      onMessageSubmitted?.();
    });
  }, [onSubmitMessageReady, onMessageSubmitted]);

  useEffect(() => {
    onAddQueryContextReady?.((item: QueryDataContext) => {
      setContextItems((prev) => (prev.some((c) => c.id === item.id) ? prev : [...prev, item]));
    });
  }, [onAddQueryContextReady]);

  const handleImport = useCallback(
    (raw: unknown) => {
      const { ok, error } = conversations.importData(String(raw ?? ''));
      notify(ok ? 'Conversations imported' : (error ?? 'Import failed'));
    },
    [conversations, notify],
  );

  // Hand conversation controls to the host so it can render them in its own
  // sidebar, the way the toolkit's onControlsReady did.
  //
  // Keyed on what the list actually displays — ids, names, selection, search,
  // busy — rather than on the conversation objects. Those change on every
  // streamed token, and handing the host a new object each time would push a
  // setState (and a re-render of the whole app shell) per token.
  const listSignature = useMemo(
    () => conversations.filtered.map((c) => `${c.id}:${c.name}`).join('|'),
    [conversations.filtered],
  );

  const controlsRef = useRef(conversations);
  controlsRef.current = conversations;

  const controls = useMemo<ChatSidebarControlHandlers>(
    () => ({
      conversations: controlsRef.current.conversations,
      filteredConversations: controlsRef.current.filtered,
      selectedConversationId: selected?.id ?? null,
      searchTerm: controlsRef.current.searchTerm,
      onSearchTermChange: (term: string) => controlsRef.current.setSearchTerm(term),
      onSelectConversation: (id: string) => controlsRef.current.select(id),
      onNewConversation: () => {
        createConversation();
      },
      onRenameConversation: (id: string, name: string) =>
        controlsRef.current.rename(id, name),
      onDeleteConversation: (id: string) => controlsRef.current.remove(id),
      onClearConversations: () => controlsRef.current.clearAll(),
      onExportData: () => controlsRef.current.exportData(),
      onImportConversations: handleImport,
      busy,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [listSignature, conversations.searchTerm, selected?.id, busy, createConversation, handleImport],
  );

  useEffect(() => {
    onControlsReady?.(controls);
  }, [onControlsReady, controls]);

  const handleRegenerate = useCallback(() => {
    const lastUser = [...messages].reverse().find((m) => m.role === 'user' && !m.error);
    if (!lastUser) return;
    // Drop the previous answer (and the user turn we are about to re-add).
    const tail = messages.length - messages.lastIndexOf(lastUser);
    void send(lastUser.content, { deleteCount: tail });
  }, [messages, send]);

  const handleEdit = useCallback(
    (message: { id: string; content: string }) => {
      // Count from the real array: hidden messages sit between the visible
      // ones, so a count derived from the rendered list truncates too little.
      const at = messages.findIndex((m) => m.id === message.id);
      if (at < 0) return;
      void send(message.content, { deleteCount: messages.length - at });
    },
    [messages, send],
  );

  const handleDelete = useCallback(
    (messageId: string) => {
      setMessages((prev) => prev.filter((m) => m.id !== messageId));
    },
    [setMessages],
  );

  const visibleMessages = messages.filter((m) => !m.hidden);
  const workflowName = title || 'Chat';

  return (
    <section
      className={`relative flex h-full w-full flex-col overflow-hidden bg-white dark:bg-black ${
        className ?? ''
      }`}
      data-theme={theme}
    >
      {features.headerMenu ? (
        <ChatHeader
          workflowName={workflowName}
          hasMessages={visibleMessages.length > 0}
          features={features}
          theme={theme}
          onThemeChange={onThemeChange}
          chatHistory={chatHistory}
          onChatHistoryChange={setChatHistory}
          onNewConversation={() => createConversation()}
          busy={busy}
          uploadUrlBase={endpoint.uploadUrlBase}
          uploadConfigTemplateJson={uploadConfigTemplateJson}
          uploadHiddenMessageTemplate={uploadHiddenMessageTemplate}
          getActiveConversationId={() => selectedIdRef.current}
          onSendHiddenMessage={(message, uploadConversationId) =>
            void send(message, { hidden: true, uploadConversationId })
          }
          onChatVideoUploadComplete={onChatVideoUploadComplete}
          onUploadFlowActiveChange={setUploadFlowActive}
          onNotify={notify}
        />
      ) : null}

      <div
        ref={logRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto"
        role="log"
        aria-live="polite"
        aria-busy={busy}
      >
        {!hydrated ? null : visibleMessages.length === 0 && !features.headerMenu ? (
          <p className="p-4 text-sm text-gray-500 dark:text-gray-400">
            {placeholder ?? 'Ask about your video…'}
          </p>
        ) : (
          visibleMessages.map((message) => (
            <ChatMessageView
              key={message.id}
              message={message}
              features={features}
              onEdit={handleEdit}
              onDelete={handleDelete}
              onNotify={notify}
            />
          ))
        )}
        {/* Keeps the last message clear of the floating composer. */}
        <div className="h-[162px]" ref={endRef} />
      </div>

      <ChatInput
        onSend={submitText}
        onRegenerate={handleRegenerate}
        onStop={stopTurn}
        onScrollDown={scrollDown}
        showScrollDownButton={!autoScroll}
        busy={busy}
        canRegenerate={visibleMessages.length > 1}
        workflowName={workflowName}
        features={features}
        customAgentParamsJson={customAgentParamsJson}
        contextItems={contextItems}
        onRemoveContext={(id) => setContextItems((prev) => prev.filter((c) => c.id !== id))}
        uploadUrlBase={endpoint.uploadUrlBase}
        uploadConfigTemplateJson={uploadConfigTemplateJson}
        uploadHiddenMessageTemplate={uploadHiddenMessageTemplate}
        getActiveConversationId={() => selectedIdRef.current}
        onSendHiddenMessage={(message, uploadConversationId) =>
          void send(message, { hidden: true, uploadConversationId })
        }
        onChatVideoUploadComplete={onChatVideoUploadComplete}
        onUploadFlowActiveChange={setUploadFlowActive}
        chatBlocked={uploadFlowActive}
        onNotify={notify}
      />

      {notice ? (
        <div
          role="status"
          className="pointer-events-none absolute left-1/2 top-14 z-[120] -translate-x-1/2 rounded-md bg-black/80 px-3 py-1.5 text-sm text-white shadow-lg"
        >
          {notice}
        </div>
      ) : null}

      {interaction ? (
        <div
          data-testid="hitl-modal"
          className="absolute inset-0 z-[130] flex items-center justify-center bg-black/60 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="hitl-prompt"
        >
          <div className="w-full max-w-lg rounded-lg bg-white p-5 shadow-xl dark:bg-gray-900">
            <p
              id="hitl-prompt"
              data-testid="hitl-modal-prompt"
              className="mb-4 whitespace-pre-wrap text-sm text-gray-900 dark:text-gray-100"
            >
              {interaction.prompt.text}
            </p>
            <textarea
              data-testid="hitl-modal-textarea"
              className="min-h-28 w-full rounded border border-gray-400 bg-white p-2 text-gray-900 dark:bg-black dark:text-gray-100"
              placeholder={interaction.prompt.placeholder ?? undefined}
              required={interaction.prompt.required}
              value={interactionText}
              onChange={(event) => setInteractionText(event.target.value)}
            />
            <div className="mt-4 flex justify-end">
              <button
                type="button"
                data-testid="hitl-modal-submit"
                className="rounded bg-green-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                disabled={interaction.prompt.required && !interactionText.trim()}
                onClick={submitInteraction}
              >
                Submit
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
};

export default ChatPanel;

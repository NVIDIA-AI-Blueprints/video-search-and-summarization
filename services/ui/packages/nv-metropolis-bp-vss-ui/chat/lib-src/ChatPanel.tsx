// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT AND Apache-2.0
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { ChatHeader } from './ChatHeader';
import { ChatInput } from './ChatInput';
import { ChatMessageView } from './ChatMessage';
import { createRandomId } from './id';
import { useChatStream } from './useChatStream';
import { useConversations } from './useConversations';
import type {
  AgentQuestionInteractionAnswer,
  ChatFeatureFlags,
  ChatPanelProps,
  ChatSidebarControlHandlers,
  InteractionAnswer,
  InteractionRequest,
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

/**
 * A pending human-in-the-loop prompt, tagged with the conversation whose turn
 * asked for it. The panel is shared by every conversation, so an untagged
 * prompt would render over — and be answered by — whichever one is selected
 * when it arrives.
 */
interface PendingInteraction {
  request: InteractionRequest;
  conversationId: string;
}

const isQuestionInteraction = (
  request: InteractionRequest,
): request is Extract<InteractionRequest, { questions: unknown }> => 'questions' in request;

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
  const [pendingInteraction, setPendingInteraction] = useState<PendingInteraction | null>(null);
  const [interactionText, setInteractionText] = useState('');
  const [interactionAnswers, setInteractionAnswers] = useState<Record<string, string[]>>({});
  const [interactionOtherAnswers, setInteractionOtherAnswers] = useState<Record<string, string>>(
    {},
  );
  const interactionResolveRef = useRef<((value: InteractionAnswer) => void) | null>(null);
  const pendingInteractionRef = useRef<PendingInteraction | null>(null);
  pendingInteractionRef.current = pendingInteraction;

  const messages = selected?.messages ?? [];
  const logRef = useRef<HTMLDivElement | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const selectedIdRef = useRef<string | undefined>(selected?.id);
  selectedIdRef.current = selected?.id;

  const conversationIdRef = useRef(conversationId);
  conversationIdRef.current = conversationId;

  const config = useMemo(() => ({ ...endpoint, conversationId }), [endpoint, conversationId]);

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
    (request: InteractionRequest, originatingConversationId: string) =>
      new Promise<InteractionAnswer>((resolve) => {
        const pending = {
          request,
          conversationId: originatingConversationId,
        };
        pendingInteractionRef.current = pending;
        setPendingInteraction(pending);
        setInteractionText('');
        setInteractionAnswers({});
        setInteractionOtherAnswers({});
        interactionResolveRef.current = resolve;
      }),
    [],
  );

  const resolveInteraction = useCallback((answer: InteractionAnswer) => {
    const resolve = interactionResolveRef.current;
    interactionResolveRef.current = null;
    pendingInteractionRef.current = null;
    setPendingInteraction(null);
    setInteractionText('');
    setInteractionAnswers({});
    setInteractionOtherAnswers({});
    resolve?.(answer);
  }, []);

  const { busy, send, abort } = useChatStream(config, {
    messages,
    setMessages,
    chatHistory,
    onAnswer: handleAnswer,
    onAnswerComplete,
    onBusyChange,
    isConversationStale,
    onInteraction: requestInteraction,
    onInteractionResolved: (interactionId) => {
      if (pendingInteractionRef.current?.request.interaction_id === interactionId) {
        resolveInteraction(null);
      }
    },
  });

  // Only the conversation that asked may answer: the prompt is hidden while
  // another one is selected, and re-checked here so a stale render cannot
  // route the reply to the wrong execution.
  const interaction =
    pendingInteraction?.conversationId === conversationId ? pendingInteraction.request : null;

  useEffect(() => {
    if (!interaction || !isQuestionInteraction(interaction)) return;
    const delay = interaction.expires_at_ms - Date.now();
    if (delay <= 0) {
      resolveInteraction(null);
      return;
    }
    const timeout = setTimeout(() => resolveInteraction(null), delay);
    return () => clearTimeout(timeout);
  }, [interaction, resolveInteraction]);

  const questionAnswers = useCallback(
    (questionId: string, hasOptions: boolean): string[] => {
      const selected = interactionAnswers[questionId] ?? [];
      const entered = interactionOtherAnswers[questionId] ?? '';
      const other = entered.trim() || undefined;
      return hasOptions ? [...selected, ...(other ? [other] : [])] : other ? [other] : [];
    },
    [interactionAnswers, interactionOtherAnswers],
  );

  const canSubmitInteraction =
    !!interaction &&
    (isQuestionInteraction(interaction)
      ? interaction.questions.every((question) => {
          const answers = questionAnswers(question.question_id, question.options.length > 0);
          return answers.length > 0 && (question.multi_select || answers.length === 1);
        })
      : !interaction.prompt.required || !!interactionText.trim());

  const submitInteraction = useCallback(() => {
    if (!interaction || !canSubmitInteraction) return;
    if (!isQuestionInteraction(interaction)) {
      resolveInteraction(interactionText);
      return;
    }
    const response: AgentQuestionInteractionAnswer = {
      type: 'questions',
      answers: Object.fromEntries(
        interaction.questions.map((question) => [
          question.question_id,
          questionAnswers(question.question_id, question.options.length > 0),
        ]),
      ),
    };
    resolveInteraction(response);
  }, [canSubmitInteraction, interaction, interactionText, questionAnswers, resolveInteraction]);

  const selectQuestionOption = useCallback(
    (questionId: string, label: string, multiSelect: boolean, checked: boolean) => {
      setInteractionAnswers((current) => {
        if (!multiSelect) return { ...current, [questionId]: checked ? [label] : [] };
        const selected = current[questionId] ?? [];
        return {
          ...current,
          [questionId]: checked
            ? [...selected.filter((answer) => answer !== label), label]
            : selected.filter((answer) => answer !== label),
        };
      });
      if (!multiSelect && checked) {
        setInteractionOtherAnswers((current) => ({
          ...current,
          [questionId]: '',
        }));
      }
    },
    [],
  );

  const setQuestionOther = useCallback(
    (questionId: string, value: string, multiSelect: boolean) => {
      setInteractionOtherAnswers((current) => ({
        ...current,
        [questionId]: value,
      }));
      if (!multiSelect && value) {
        setInteractionAnswers((current) => ({ ...current, [questionId]: [] }));
      }
    },
    [],
  );

  const stopTurn = useCallback(() => {
    const pending = pendingInteractionRef.current?.request;
    resolveInteraction(pending && !isQuestionInteraction(pending) ? '/cancel' : null);
    abort();
  }, [abort, resolveInteraction]);

  // Switching away leaves the prompt waiting: the turn is still running and the
  // modal returns with its conversation. Discarding the conversation is
  // different — nobody can answer for it any more, so decline the prompt
  // instead of leaving the agent blocked on a reply that will never arrive.
  const declineInteractionFor = useCallback(
    (discardedId?: string) => {
      const pending = pendingInteractionRef.current;
      if (!pending) return;
      if (discardedId && pending.conversationId !== discardedId) return;
      if (isQuestionInteraction(pending.request)) {
        resolveInteraction(null);
        abort();
      } else {
        resolveInteraction('/cancel');
      }
    },
    [abort, resolveInteraction],
  );

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
      onRenameConversation: (id: string, name: string) => controlsRef.current.rename(id, name),
      onDeleteConversation: (id: string) => {
        declineInteractionFor(id);
        controlsRef.current.remove(id);
      },
      onClearConversations: () => {
        declineInteractionFor();
        controlsRef.current.clearAll();
      },
      onExportData: () => controlsRef.current.exportData(),
      onImportConversations: handleImport,
      busy,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      listSignature,
      conversations.searchTerm,
      selected?.id,
      busy,
      createConversation,
      declineInteractionFor,
      handleImport,
    ],
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
            {isQuestionInteraction(interaction) ? (
              <div className="max-h-[70vh] space-y-5 overflow-y-auto pr-1">
                <p
                  id="hitl-prompt"
                  data-testid="hitl-modal-prompt"
                  className="text-sm font-medium text-gray-900 dark:text-gray-100"
                >
                  The agent needs your input to continue.
                </p>
                {interaction.questions.map((question, questionIndex) => {
                  const selected = interactionAnswers[question.question_id] ?? [];
                  const inputId = `hitl-question-${question.question_id}`;
                  return (
                    <fieldset
                      key={question.question_id}
                      data-testid={inputId}
                      className="space-y-2 rounded border border-gray-300 p-3 dark:border-gray-700"
                    >
                      <legend className="px-1 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                        {question.header || `Question ${questionIndex + 1}`}
                      </legend>
                      <p className="whitespace-pre-wrap text-sm text-gray-900 dark:text-gray-100">
                        {question.prompt}
                      </p>
                      {question.options.length ? (
                        <div className="space-y-2">
                          {question.options.map((option, optionIndex) => {
                            const checked = selected.includes(option.label);
                            return (
                              <label
                                key={option.label}
                                className="flex cursor-pointer items-start gap-2 rounded border border-gray-200 p-2 text-sm text-gray-900 dark:border-gray-700 dark:text-gray-100"
                              >
                                <input
                                  data-testid={`hitl-option-${question.question_id}-${optionIndex}`}
                                  className="mt-0.5"
                                  type={question.multi_select ? 'checkbox' : 'radio'}
                                  name={`hitl-${interaction.interaction_id}-${question.question_id}`}
                                  checked={checked}
                                  onChange={(event) =>
                                    selectQuestionOption(
                                      question.question_id,
                                      option.label,
                                      question.multi_select,
                                      event.target.checked,
                                    )
                                  }
                                />
                                <span>
                                  <span className="block font-medium">{option.label}</span>
                                  {option.description ? (
                                    <span className="block text-xs text-gray-500 dark:text-gray-400">
                                      {option.description}
                                    </span>
                                  ) : null}
                                </span>
                              </label>
                            );
                          })}
                        </div>
                      ) : null}
                      {!question.options.length || question.allow_other ? (
                        <textarea
                          data-testid={`hitl-answer-${question.question_id}`}
                          className="min-h-20 w-full rounded border border-gray-400 bg-white p-2 text-sm text-gray-900 dark:bg-black dark:text-gray-100"
                          aria-label={question.options.length ? 'Other answer' : 'Answer'}
                          placeholder={
                            question.options.length ? 'Other answer' : 'Enter your answer'
                          }
                          value={interactionOtherAnswers[question.question_id] ?? ''}
                          onChange={(event) =>
                            setQuestionOther(
                              question.question_id,
                              event.target.value,
                              question.multi_select,
                            )
                          }
                        />
                      ) : null}
                    </fieldset>
                  );
                })}
              </div>
            ) : (
              <>
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
                  onKeyDown={(event) => {
                    if (
                      event.key === 'Enter' &&
                      !event.shiftKey &&
                      !event.nativeEvent.isComposing
                    ) {
                      event.preventDefault();
                      submitInteraction();
                    }
                  }}
                />
              </>
            )}
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                data-testid="hitl-modal-cancel"
                className="rounded border border-gray-400 px-4 py-2 text-sm font-medium text-gray-800 dark:text-gray-100"
                onClick={stopTurn}
              >
                Cancel run
              </button>
              <button
                type="button"
                data-testid="hitl-modal-submit"
                className="rounded bg-green-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                disabled={!canSubmitInteraction}
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

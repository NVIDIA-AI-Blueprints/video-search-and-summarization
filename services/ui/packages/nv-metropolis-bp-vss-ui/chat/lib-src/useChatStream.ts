// SPDX-License-Identifier: MIT
import { useCallback, useEffect, useRef, useState } from 'react';

import { SseParser, type InteractionRequest } from './sse';
import type {
  CallerInfo,
  ChatEndpointConfig,
  ChatMessage,
  ChatStep,
  QueryDataContext,
} from './types';

let seq = 0;
export const nextId = (): string => `m${Date.now().toString(36)}-${seq++}`;

export interface SendOptions {
  /** Drop this many trailing messages first — regenerate (1) and edit (n). */
  deleteCount?: number;
  /** Sent to the agent but never rendered. */
  hidden?: boolean;
  /** Conversation that was active when an upload started. */
  uploadConversationId?: string;
  /** Merged into the request body for this turn. */
  params?: Record<string, string | number | boolean>;
  /** Chips to fold into the message as a `[Context: …]` prefix. */
  context?: QueryDataContext[];
}

export interface UseChatStreamOptions {
  messages: ChatMessage[];
  setMessages: (updater: (prev: ChatMessage[]) => ChatMessage[]) => void;
  /** Send the whole thread rather than only the latest turn. */
  chatHistory?: boolean;
  /** Returning a string renders it as the answer's caller-info card. */
  onAnswer?: (answer: string) => CallerInfo | boolean | void;
  onAnswerComplete?: () => void;
  onBusyChange?: (busy: boolean) => void;
  /** Called when the turn's conversation is no longer the selected one. */
  isConversationStale?: (uploadConversationId: string) => boolean;
}

export interface UseChatStreamResult {
  busy: boolean;
  send: (text: string, options?: SendOptions) => Promise<void>;
  abort: () => void;
  interaction: InteractionRequest | null;
  dismissInteraction: () => void;
}

/**
 * Turn context chips into the prefix the backend sees.
 *
 * Only `data` crosses the wire: `id`, `label` and `contextType` exist for the
 * chip UI. `contextType` is stripped even if a caller duplicated it inside
 * `data`, matching the toolkit's Chat.tsx exactly — the agent prompt is written
 * against that shape.
 */
export function buildContextPrefix(items: QueryDataContext[]): string {
  if (!items.length) return '';
  const payload = items.map(({ data }) => {
    const rest: Record<string, unknown> = { ...(data as Record<string, unknown>) };
    delete rest.contextType;
    return rest;
  });
  return `[Context: ${JSON.stringify(payload)}]`;
}

/**
 * Drives one conversation against a BYO agent backend.
 *
 * Posts the OpenAI-shaped body the contract expects and consumes the SSE
 * response, appending tokens to the in-flight assistant message and collecting
 * tool steps alongside it.
 */
export function useChatStream(
  endpoint: ChatEndpointConfig,
  options: UseChatStreamOptions,
): UseChatStreamResult {
  const { messages, setMessages, chatHistory = true } = options;
  const [busy, setBusy] = useState(false);
  const [interaction, setInteraction] = useState<InteractionRequest | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Callbacks and message state are read through refs so `send` stays stable:
  // it is handed to embedders via onSubmitMessageReady, and a new identity on
  // every token would make them re-register mid-stream.
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const endpointRef = useRef(endpoint);
  endpointRef.current = endpoint;
  const busyRef = useRef(false);

  useEffect(() => {
    optionsRef.current.onBusyChange?.(busy);
  }, [busy]);

  const setBusyBoth = useCallback((value: boolean) => {
    busyRef.current = value;
    setBusy(value);
  }, []);

  const abort = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusyBoth(false);
  }, [setBusyBoth]);

  // Abandon an in-flight turn if the panel unmounts, so the reader loop does
  // not keep writing into a dead component.
  useEffect(() => () => abortRef.current?.abort(), []);

  const send = useCallback(
    async (text: string, sendOptions: SendOptions = {}) => {
      const { deleteCount = 0, hidden, uploadConversationId, params, context = [] } = sendOptions;

      const prefix = buildContextPrefix(context);
      const body = prefix ? (text.trim() ? `${prefix}\n\n${text}` : prefix) : text;
      const trimmed = body.trim();
      if (!trimmed || busyRef.current) return;

      const userMsg: ChatMessage = {
        id: nextId(),
        role: 'user',
        content: trimmed,
        hidden,
        uploadConversationId,
        timestamp: Date.now(),
      };
      const replyId = nextId();

      // Compute history from the same slice we are about to render, so a
      // regenerate sends the thread as it will look, not as it looked.
      const kept = deleteCount
        ? messagesRef.current.slice(0, Math.max(0, messagesRef.current.length - deleteCount))
        : messagesRef.current;
      const history = kept
        .filter((m) => !m.error)
        .map((m) => ({ role: m.role, content: m.content }));

      setMessages((prev) => {
        const base = deleteCount ? prev.slice(0, Math.max(0, prev.length - deleteCount)) : prev;
        return [
          ...base,
          userMsg,
          { id: replyId, role: 'assistant', content: '', steps: [], streaming: true },
        ];
      });
      setBusyBoth(true);

      const controller = new AbortController();
      abortRef.current = controller;

      const patchReply = (fn: (m: ChatMessage) => ChatMessage) =>
        setMessages((prev) => prev.map((m) => (m.id === replyId ? fn(m) : m)));

      let answer = '';
      let failed = '';
      try {
        const res = await fetch(endpointRef.current.url, {
          method: 'POST',
          signal: controller.signal,
          headers: {
            'Content-Type': 'application/json',
            'Conversation-Id': endpointRef.current.conversationId,
            'User-Message-ID': userMsg.id,
            ...(endpointRef.current.headers ?? {}),
          },
          body: JSON.stringify({
            // Custom params first so the fixed fields always win, as the
            // toolkit does — a param named `messages` must not shadow the turn.
            ...(endpointRef.current.extraParams ?? {}),
            ...(params ?? {}),
            messages: chatHistory
              ? [...history, { role: 'user', content: trimmed }]
              : [{ role: 'user', content: trimmed }],
          }),
        });

        if (!res.ok) throw new Error(`backend returned HTTP ${res.status}`);
        if (!res.body) throw new Error('backend returned no response body');

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        const parser = new SseParser();
        const steps: ChatStep[] = [];

        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          for (const ev of parser.feed(decoder.decode(value, { stream: true }))) {
            if (ev.kind === 'token') {
              answer += ev.text;
              patchReply((m) => ({ ...m, content: answer }));
            } else if (ev.kind === 'step') {
              // Steps arrive keyed by id; a later frame updates an earlier step.
              const at = steps.findIndex((s) => s.id === ev.step.id);
              if (at >= 0) steps[at] = ev.step;
              else steps.push(ev.step);
              patchReply((m) => ({ ...m, steps: [...steps] }));
            } else if (ev.kind === 'error') {
              failed = ev.message;
            } else if (ev.kind === 'interaction') {
              setInteraction(ev.request);
            } else {
              patchReply((m) => ({ ...m, streaming: false }));
            }
          }
        }

        // An upload auto-prompt whose conversation the user has since left
        // would drop its answer into the wrong thread.
        const stale =
          !!uploadConversationId &&
          !!optionsRef.current.isConversationStale?.(uploadConversationId);

        const callerInfo =
          !stale && answer ? optionsRef.current.onAnswer?.(answer) : undefined;

        patchReply((m) => ({
          ...m,
          content: answer,
          streaming: false,
          error: failed || undefined,
          callerInfo: typeof callerInfo === 'string' ? callerInfo : undefined,
        }));
        if (!stale) optionsRef.current.onAnswerComplete?.();
      } catch (err) {
        const aborted = err instanceof DOMException && err.name === 'AbortError';
        patchReply((m) => ({
          ...m,
          streaming: false,
          error: aborted ? 'cancelled' : err instanceof Error ? err.message : String(err),
        }));
      } finally {
        abortRef.current = null;
        setBusyBoth(false);
      }
    },
    [chatHistory, setMessages, setBusyBoth],
  );

  const dismissInteraction = useCallback(() => setInteraction(null), []);

  return { busy, send, abort, interaction, dismissInteraction };
}

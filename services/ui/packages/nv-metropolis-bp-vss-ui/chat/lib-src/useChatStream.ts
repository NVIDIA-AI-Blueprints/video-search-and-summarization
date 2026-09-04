// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT AND Apache-2.0
import { useCallback, useEffect, useRef, useState } from 'react';

import {
  assertAgentApiEventScope,
  createAgentApiChatState,
  agentApiEventToChatEvents,
  AgentApiSseParser,
  type AgentApiChatEvent,
  type AgentApiRun,
} from './agentApi';
import { SseParser, type SseEvent } from './sse';
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
 * Drives one conversation through either the structured VSS agent API or
 * the legacy OpenAI-shaped chat-SSE transport. Both paths append tokens to the
 * in-flight assistant message and collect tool steps alongside it.
 */
export function useChatStream(
  endpoint: ChatEndpointConfig,
  options: UseChatStreamOptions,
): UseChatStreamResult {
  const { messages, setMessages, chatHistory = true } = options;
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const cancelUrlRef = useRef<string | null>(null);

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

  const cancelAgentRun = useCallback(() => {
    const cancelUrl = cancelUrlRef.current;
    cancelUrlRef.current = null;
    if (!cancelUrl) return;
    void fetch(cancelUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    }).catch(() => undefined);
  }, []);

  const abort = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    cancelAgentRun();
    setBusyBoth(false);
  }, [cancelAgentRun, setBusyBoth]);

  // Abandon an in-flight turn if the panel unmounts, so the reader loop does
  // not keep writing into a dead component.
  useEffect(
    () => () => {
      abortRef.current?.abort();
      cancelAgentRun();
    },
    [cancelAgentRun],
  );

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
      let agentTerminal = false;
      const artifactEnvelopes: string[] = [];
      const steps: ChatStep[] = [];

      const consume = (events: Array<SseEvent | AgentApiChatEvent>) => {
        for (const ev of events) {
          if (ev.kind === 'token') {
            answer += ev.text;
            patchReply((m) => ({ ...m, content: answer }));
          } else if (ev.kind === 'step') {
            // Steps arrive keyed by id; a later frame updates an earlier step.
            const at = steps.findIndex((step) => step.id === ev.step.id);
            if (at >= 0) steps[at] = ev.step;
            else steps.push(ev.step);
            patchReply((m) => ({ ...m, steps: [...steps] }));
          } else if (ev.kind === 'artifact') {
            artifactEnvelopes.push(ev.envelope);
          } else if (ev.kind === 'error') {
            failed = ev.message;
          } else {
            agentTerminal = true;
            for (let index = 0; index < steps.length; index += 1) {
              if (steps[index].status === 'in_progress') {
                steps[index] = { ...steps[index], status: 'complete' };
              }
            }
            patchReply((m) => ({ ...m, streaming: false, steps: [...steps] }));
          }
        }
      };

      try {
        if (endpointRef.current.transport === 'agent-api') {
          const agentEndpoint = endpointRef.current;
          const baseUrl = agentEndpoint.url.replace(/\/$/, '');
          const threadId = agentEndpoint.conversationId;
          const createResponse = await fetch(`${baseUrl}/runs`, {
            method: 'POST',
            signal: controller.signal,
            headers: {
              'Content-Type': 'application/json',
              'Idempotency-Key': userMsg.id,
              ...(agentEndpoint.headers ?? {}),
            },
            body: JSON.stringify({
              thread_id: threadId,
              input: [{ role: 'user', content: trimmed }],
              history: chatHistory ? history : [],
              surface: agentEndpoint.surface ?? 'vss-ui',
              metadata: {
                ...(agentEndpoint.extraParams ?? {}),
                ...(params ?? {}),
              },
            }),
          });
          if (!createResponse.ok) {
            throw new Error(`agent API returned HTTP ${createResponse.status}`);
          }
          const run = (await createResponse.json()) as Partial<AgentApiRun>;
          if (
            typeof run.run_id !== 'string' ||
            typeof run.events_url !== 'string' ||
            typeof run.cancel_url !== 'string'
          ) {
            throw new Error('agent API returned an invalid run');
          }
          cancelUrlRef.current = run.cancel_url;

          const eventsResponse = await fetch(run.events_url, {
            signal: controller.signal,
            headers: {
              Accept: 'text/event-stream',
              ...(agentEndpoint.headers ?? {}),
            },
          });
          if (!eventsResponse.ok) {
            throw new Error(`agent API returned HTTP ${eventsResponse.status}`);
          }
          if (!eventsResponse.body) throw new Error('agent API returned no event stream');

          const reader = eventsResponse.body.getReader();
          const decoder = new TextDecoder();
          const parser = new AgentApiSseParser();
          const agentState = createAgentApiChatState();
          const mapEvents = (events: ReturnType<AgentApiSseParser['feed']>) =>
            events.flatMap((event) => {
              assertAgentApiEventScope(event, run.run_id!, threadId);
              return agentApiEventToChatEvents(event, agentState, agentEndpoint.mediaProxyUrl);
            });
          try {
            for (;;) {
              const { done, value } = await reader.read();
              if (done) break;
              consume(mapEvents(parser.feed(decoder.decode(value, { stream: true }))));
            }
            const trailing = [...parser.feed(decoder.decode()), ...parser.finish()];
            consume(mapEvents(trailing));
          } finally {
            reader.releaseLock();
          }
          if (!agentTerminal) {
            throw new Error('agent API event stream ended before the run completed');
          }
          cancelUrlRef.current = null;
        } else {
          const response = await fetch(endpointRef.current.url, {
            method: 'POST',
            signal: controller.signal,
            headers: {
              'Content-Type': 'application/json',
              'Conversation-Id': endpointRef.current.conversationId,
              'User-Message-ID': userMsg.id,
              ...(endpointRef.current.headers ?? {}),
            },
            body: JSON.stringify({
              // Custom params first so fixed fields win: a param named
              // `messages` must not shadow the turn.
              ...(endpointRef.current.extraParams ?? {}),
              ...(params ?? {}),
              messages: chatHistory
                ? [...history, { role: 'user', content: trimmed }]
                : [{ role: 'user', content: trimmed }],
            }),
          });
          if (!response.ok) throw new Error(`backend returned HTTP ${response.status}`);
          if (!response.body) throw new Error('backend returned no response body');

          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          const parser = new SseParser();
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            consume(parser.feed(decoder.decode(value, { stream: true })));
          }
          consume(parser.feed(decoder.decode()));
        }

        // An upload auto-prompt whose conversation the user has since left
        // would drop its answer into the wrong thread.
        const stale =
          !!uploadConversationId &&
          !!optionsRef.current.isConversationStale?.(uploadConversationId);

        const callbackAnswer = [answer, ...artifactEnvelopes].filter(Boolean).join('\n');
        // Embedders use completion to resolve the tab that owned the in-flight
        // turn. Delivering content first can target whichever tab is active
        // now instead of the one that submitted the request.
        if (!stale) optionsRef.current.onAnswerComplete?.();
        const callerInfo =
          !stale && callbackAnswer ? optionsRef.current.onAnswer?.(callbackAnswer) : undefined;

        patchReply((m) => ({
          ...m,
          content: answer,
          streaming: false,
          error: failed || undefined,
          callerInfo: typeof callerInfo === 'string' ? callerInfo : undefined,
        }));
      } catch (err) {
        const aborted = err instanceof DOMException && err.name === 'AbortError';
        patchReply((m) => ({
          ...m,
          streaming: false,
          error: aborted ? 'cancelled' : err instanceof Error ? err.message : String(err),
        }));
      } finally {
        if (!agentTerminal) cancelAgentRun();
        cancelUrlRef.current = null;
        abortRef.current = null;
        setBusyBoth(false);
      }
    },
    [cancelAgentRun, chatHistory, setMessages, setBusyBoth],
  );

  return { busy, send, abort };
}

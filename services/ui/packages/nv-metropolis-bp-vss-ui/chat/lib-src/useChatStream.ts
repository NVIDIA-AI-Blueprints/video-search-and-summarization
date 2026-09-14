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
  InteractionAnswer,
  InteractionRequest,
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
  onInteraction?: (
    interaction: InteractionRequest,
    conversationId: string,
  ) => Promise<InteractionAnswer>;
  onInteractionResolved?: (interactionId: string) => void;
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
      const activeStructuredInteractions = new Set<string>();
      const resolvedStructuredInteractions = new Set<string>();
      const interactionResolutionWaiters = new Map<string, Set<(resolved: boolean) => void>>();
      let interactionFailure: unknown;
      const turnConversationId = endpointRef.current.conversationId;

      const markStructuredInteractionResolved = (interactionId: string): void => {
        resolvedStructuredInteractions.add(interactionId);
        for (const resolve of interactionResolutionWaiters.get(interactionId) ?? []) {
          resolve(true);
        }
        interactionResolutionWaiters.delete(interactionId);
      };

      const waitForStructuredInteractionResolution = (
        interactionId: string,
        timeoutMs = 5_000,
      ): Promise<boolean> => {
        if (resolvedStructuredInteractions.has(interactionId)) return Promise.resolve(true);
        return new Promise((resolve) => {
          let settled = false;
          const finish = (resolved: boolean): void => {
            if (settled) return;
            settled = true;
            clearTimeout(timeout);
            controller.signal.removeEventListener('abort', aborted);
            const waiters = interactionResolutionWaiters.get(interactionId);
            waiters?.delete(finish);
            if (waiters?.size === 0) interactionResolutionWaiters.delete(interactionId);
            resolve(resolved);
          };
          const aborted = (): void => finish(false);
          const timeout = setTimeout(() => finish(false), timeoutMs);
          const waiters = interactionResolutionWaiters.get(interactionId) ?? new Set();
          waiters.add(finish);
          interactionResolutionWaiters.set(interactionId, waiters);
          controller.signal.addEventListener('abort', aborted, { once: true });
        });
      };

      const beginStructuredInteraction = (
        interaction: Extract<InteractionRequest, { questions: unknown }>,
        answerInteraction: NonNullable<UseChatStreamOptions['onInteraction']>,
      ): void => {
        if (activeStructuredInteractions.size) {
          throw new Error('The agent emitted overlapping structured interactions');
        }
        activeStructuredInteractions.add(interaction.interaction_id);
        void (async () => {
          const interactionAnswer = await answerInteraction(interaction, turnConversationId);
          if (interactionAnswer === null) return;
          if (typeof interactionAnswer === 'string') {
            throw new Error('Structured agent interaction UI returned an invalid response');
          }
          const response = await fetch(interaction.response_url, {
            method: 'POST',
            signal: controller.signal,
            headers: {
              'Content-Type': 'application/json',
              ...(endpointRef.current.headers ?? {}),
            },
            body: JSON.stringify({
              interaction_id: interaction.interaction_id,
              response: interactionAnswer,
            }),
          });
          // Another OpenClaw operator may win the resolution race after the
          // local Submit. A matching interaction.resolved event is definitive:
          // keep consuming the resumed run instead of turning the late 409 into
          // an abort/cancel. Uncorrelated conflicts still fail normally.
          if (
            !response.ok &&
            (response.status !== 409 ||
              !(await waitForStructuredInteractionResolution(interaction.interaction_id)))
          ) {
            throw new Error(`interaction response returned HTTP ${response.status}`);
          }
        })()
          .catch((error: unknown) => {
            // Wake a reader blocked on the SSE stream so the response failure
            // is surfaced immediately and the still-waiting run is cancelled.
            if (!controller.signal.aborted) {
              interactionFailure = error;
              controller.abort();
            }
          })
          .finally(() => activeStructuredInteractions.delete(interaction.interaction_id));
      };

      const consume = async (events: Array<SseEvent | AgentApiChatEvent>) => {
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
          } else if (ev.kind === 'interaction') {
            const answerInteraction = optionsRef.current.onInteraction;
            if (!answerInteraction) throw new Error('Interactive agent response UI is unavailable');
            if ('questions' in ev.interaction) {
              beginStructuredInteraction(ev.interaction, answerInteraction);
              continue;
            }
            const interactionAnswer = await answerInteraction(ev.interaction, turnConversationId);
            if (interactionAnswer === null) continue;
            if (ev.interaction.prompt.input_type !== 'text') {
              throw new Error(`Unsupported interaction type: ${ev.interaction.prompt.input_type}`);
            }
            if (typeof interactionAnswer !== 'string') {
              throw new Error('Text interaction UI returned an invalid response');
            }
            const url = new URL(endpointRef.current.url, window.location.origin);
            url.searchParams.set('interaction', ev.interaction.response_url);
            const interactionUrl = `${url.pathname}${url.search}`;
            const interactionResponse = await fetch(interactionUrl, {
              method: 'POST',
              signal: controller.signal,
              headers: {
                'Content-Type': 'application/json',
                ...(endpointRef.current.headers ?? {}),
              },
              body: JSON.stringify({ response: { type: 'text', text: interactionAnswer } }),
            });
            if (!interactionResponse.ok) {
              throw new Error(`interaction response returned HTTP ${interactionResponse.status}`);
            }
          } else if (ev.kind === 'interaction-resolved') {
            markStructuredInteractionResolved(ev.interactionId);
            activeStructuredInteractions.delete(ev.interactionId);
            optionsRef.current.onInteractionResolved?.(ev.interactionId);
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
              return agentApiEventToChatEvents(event, agentState, agentEndpoint.mediaProxyUrl, run.respond_url);
            });
          try {
            for (;;) {
              const { done, value } = await reader.read();
              if (done) break;
              await consume(mapEvents(parser.feed(decoder.decode(value, { stream: true }))));
            }
            const trailing = [...parser.feed(decoder.decode()), ...parser.finish()];
            await consume(mapEvents(trailing));
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
          const parser = new SseParser(endpointRef.current.mediaProxyUrl);
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            await consume(parser.feed(decoder.decode(value, { stream: true })));
          }
          await consume([...parser.feed(decoder.decode()), ...parser.finish()]);
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
        const reported = interactionFailure ?? err;
        const aborted = interactionFailure === undefined && err instanceof DOMException && err.name === 'AbortError';
        patchReply((m) => ({
          ...m,
          streaming: false,
          error: aborted ? 'cancelled' : reported instanceof Error ? reported.message : String(reported),
        }));
      } finally {
        for (const waiters of interactionResolutionWaiters.values()) {
          for (const resolve of waiters) resolve(false);
        }
        interactionResolutionWaiters.clear();
        for (const interactionId of activeStructuredInteractions) {
          optionsRef.current.onInteractionResolved?.(interactionId);
        }
        activeStructuredInteractions.clear();
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

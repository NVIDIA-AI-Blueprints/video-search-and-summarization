// SPDX-License-Identifier: MIT
import { useCallback, useRef, useState } from 'react';

import { SseParser } from './sse';
import type { ChatEndpointConfig, ChatMessage, ChatStep } from './types';

let seq = 0;
const nextId = () => `m${Date.now().toString(36)}-${seq++}`;

export interface UseChatStreamResult {
  messages: ChatMessage[];
  busy: boolean;
  send: (text: string) => Promise<void>;
  abort: () => void;
  reset: () => void;
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
  onAnswer?: (answer: string) => void,
): UseChatStreamResult {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const abort = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
  }, []);

  const reset = useCallback(() => {
    abort();
    setMessages([]);
  }, [abort]);

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || busy) return;

      const history = messages
        .filter((m) => !m.error)
        .map((m) => ({ role: m.role, content: m.content }));

      const userMsg: ChatMessage = { id: nextId(), role: 'user', content: trimmed };
      const replyId = nextId();
      setMessages((prev) => [
        ...prev,
        userMsg,
        { id: replyId, role: 'assistant', content: '', steps: [], streaming: true },
      ]);
      setBusy(true);

      const controller = new AbortController();
      abortRef.current = controller;

      const patchReply = (fn: (m: ChatMessage) => ChatMessage) =>
        setMessages((prev) => prev.map((m) => (m.id === replyId ? fn(m) : m)));

      let answer = '';
      try {
        const res = await fetch(endpoint.url, {
          method: 'POST',
          signal: controller.signal,
          headers: {
            'Content-Type': 'application/json',
            'Conversation-Id': endpoint.conversationId,
            ...(endpoint.headers ?? {}),
          },
          body: JSON.stringify({
            messages: [...history, { role: 'user', content: trimmed }],
            ...(endpoint.extraParams ?? {}),
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
            } else {
              patchReply((m) => ({ ...m, streaming: false }));
            }
          }
        }
        patchReply((m) => ({ ...m, content: answer, streaming: false }));
        if (answer) onAnswer?.(answer);
      } catch (err) {
        const aborted = err instanceof DOMException && err.name === 'AbortError';
        patchReply((m) => ({
          ...m,
          streaming: false,
          error: aborted ? 'cancelled' : err instanceof Error ? err.message : String(err),
        }));
      } finally {
        abortRef.current = null;
        setBusy(false);
      }
    },
    [busy, endpoint, messages, onAnswer],
  );

  return { messages, busy, send, abort, reset };
}

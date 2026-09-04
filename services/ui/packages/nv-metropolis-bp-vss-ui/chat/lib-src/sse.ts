// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Parser for the legacy BYO-agent chat-SSE contract.
 *
 * The stream carries these kinds of line:
 *
 *   data: {"choices":[{"delta":{"content":"..."}}]}   assistant text
 *   data: [DONE]                                      terminal
 *   intermediate_data: {...}                          tool/skill progress
 *   error_data: {...}                                 turn-level failure
 *   interaction_data: {...}                           unsupported interaction
 *   : keepalive                                       comment, ignored
 *
 * Content is read from several shapes because backends differ: OpenAI-style
 * `choices[0].delta.content` and `choices[0].message.content`, plus the plain
 * `value` / `output` / `answer` fields some agent servers return.
 *
 * Kept free of React so it can be unit tested directly, which matters — this is
 * the one piece where a silent mistake shows up as "the agent said nothing".
 */

import type { ChatStep } from './types';

export type SseEvent =
  | { kind: 'token'; text: string }
  | { kind: 'step'; step: ChatStep }
  | { kind: 'error'; message: string }
  | { kind: 'done' };

const CONTENT_PATHS = ['value', 'output', 'answer'] as const;

/** Pull assistant text out of one parsed `data:` payload. */
export function extractContent(parsed: unknown): string {
  if (typeof parsed === 'string') return parsed;
  if (!parsed || typeof parsed !== 'object') return '';
  const obj = parsed as Record<string, any>;

  const choice = Array.isArray(obj.choices) ? obj.choices[0] : undefined;
  const fromChoice = choice?.delta?.content ?? choice?.message?.content;
  if (typeof fromChoice === 'string') return fromChoice;

  for (const path of CONTENT_PATHS) {
    if (typeof obj[path] === 'string') return obj[path];
  }
  return '';
}

/**
 * Assemble a flat list of steps into the tree their `parentId`s describe.
 *
 * Steps arrive in completion order, not tree order, and a child can land
 * before its parent. Orphans are kept at the root rather than dropped, because
 * losing a step silently is worse than showing it at the wrong depth.
 */
export function buildStepTree(steps: ChatStep[]): ChatStep[] {
  const byId = new Map<string, ChatStep>();
  for (const step of steps) byId.set(step.id, { ...step, children: [] });

  const roots: ChatStep[] = [];
  for (const step of steps) {
    const node = byId.get(step.id)!;
    const parent = step.parentId ? byId.get(step.parentId) : undefined;
    if (parent && parent !== node) parent.children!.push(node);
    else roots.push(node);
  }
  return roots;
}

const PREFIXES = {
  data: 'data: ',
  step: 'intermediate_data: ',
  error: 'error_data: ',
  interaction: 'interaction_data: ',
} as const;

/**
 * Incremental SSE reader.
 *
 * Feed it decoded chunks; it holds a partial trailing line between calls, since
 * a chunk boundary can land mid-line.
 */
export class SseParser {
  private buffer = '';
  private stepIndex = 0;

  feed(chunk: string): SseEvent[] {
    // Normalise CRLF first: a proxy that rewrites line endings would otherwise
    // leave a stray \r on every payload and break JSON.parse.
    this.buffer += chunk.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    const lines = this.buffer.split('\n');
    // The last element is either an incomplete line or '' — keep it for later.
    this.buffer = lines.pop() ?? '';

    const events: SseEvent[] = [];
    for (const raw of lines) {
      const line = raw.trimEnd();
      if (!line || line.startsWith(':')) continue; // blank or keepalive comment

      if (line.startsWith(PREFIXES.data)) {
        const payload = line.slice(PREFIXES.data.length).trim();
        if (payload === '[DONE]') {
          events.push({ kind: 'done' });
          continue;
        }
        let text = '';
        try {
          text = extractContent(JSON.parse(payload));
        } catch {
          // Not JSON: some backends stream bare text after `data: `.
          text = payload;
        }
        if (text) events.push({ kind: 'token', text });
        continue;
      }

      if (line.startsWith(PREFIXES.step)) {
        const step = this.parseStep(line.slice(PREFIXES.step.length));
        if (step) events.push({ kind: 'step', step });
        continue;
      }

      if (line.startsWith(PREFIXES.error)) {
        const message = this.parseError(line.slice(PREFIXES.error.length));
        if (message) events.push({ kind: 'error', message });
        continue;
      }

      if (line.startsWith(PREFIXES.interaction)) {
        events.push({
          kind: 'error',
          message: 'Interactive agent responses are not supported by this UI.',
        });
      }
    }
    return events;
  }

  private parseStep(payload: string): ChatStep | null {
    try {
      const d = JSON.parse(payload) as Record<string, any>;
      const status = d.status === 'complete' || d.status === 'error' ? d.status : 'in_progress';
      const parentId = d.parent_id ?? d.parentId;
      return {
        id: String(d.id ?? this.stepIndex),
        name: String(d.name ?? d.content?.name ?? 'step'),
        status,
        payload:
          typeof d.payload === 'string'
            ? d.payload
            : typeof d.content?.payload === 'string'
              ? d.content.payload
              : undefined,
        index: typeof d.index === 'number' ? d.index : this.stepIndex++,
        parentId: parentId == null ? undefined : String(parentId),
      };
    } catch {
      return null;
    }
  }

  private parseError(payload: string): string | null {
    try {
      const d = JSON.parse(payload) as Record<string, any>;
      const message = d.message ?? d.error ?? d.content?.text ?? d.content;
      return typeof message === 'string' ? message : JSON.stringify(d);
    } catch {
      return payload.trim() || null;
    }
  }

}

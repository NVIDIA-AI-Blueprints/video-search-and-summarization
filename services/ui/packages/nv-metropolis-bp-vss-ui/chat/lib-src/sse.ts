// SPDX-License-Identifier: MIT
/**
 * Parser for the BYO agent SSE contract.
 *
 * The stream carries three kinds of line:
 *
 *   data: {"choices":[{"delta":{"content":"..."}}]}   assistant text
 *   data: [DONE]                                      terminal
 *   intermediate_data: {...}                          tool/skill progress
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
 * Incremental SSE reader.
 *
 * Feed it decoded chunks; it holds a partial trailing line between calls, since
 * a chunk boundary can land mid-line.
 */
export class SseParser {
  private buffer = '';
  private stepIndex = 0;

  feed(chunk: string): SseEvent[] {
    this.buffer += chunk;
    const lines = this.buffer.split('\n');
    // The last element is either an incomplete line or '' — keep it for later.
    this.buffer = lines.pop() ?? '';

    const events: SseEvent[] = [];
    for (const raw of lines) {
      const line = raw.trimEnd();
      if (!line || line.startsWith(':')) continue; // blank or keepalive comment

      if (line.startsWith('data: ')) {
        const payload = line.slice(6).trim();
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

      if (line.startsWith('intermediate_data: ')) {
        const step = this.parseStep(line.slice('intermediate_data: '.length));
        if (step) events.push({ kind: 'step', step });
      }
    }
    return events;
  }

  private parseStep(payload: string): ChatStep | null {
    try {
      const d = JSON.parse(payload) as Record<string, any>;
      const status = d.status === 'complete' || d.status === 'error' ? d.status : 'in_progress';
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
        index: this.stepIndex++,
      };
    } catch {
      return null;
    }
  }
}

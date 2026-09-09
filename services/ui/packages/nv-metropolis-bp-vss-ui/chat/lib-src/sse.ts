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
 *   artifact_data: {...}                              structured tab payload
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

import { artifactEnvelope } from './agentApi';
import type { ChatStep } from './types';

export type SseEvent =
  | { kind: 'token'; text: string }
  | { kind: 'step'; step: ChatStep }
  | { kind: 'artifact'; envelope: string }
  | { kind: 'interaction'; interaction: InteractionRequest }
  | { kind: 'error'; message: string }
  | { kind: 'done' };

export interface InteractionRequest {
  event_type: 'interaction_required';
  execution_id: string;
  interaction_id: string;
  prompt: {
    text: string;
    input_type: string;
    placeholder?: string | null;
    required?: boolean;
  };
  response_url: string;
}

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
  event: 'event:',
  data: 'data:',
  step: 'intermediate_data: ',
  artifact: 'artifact_data: ',
  error: 'error_data: ',
  interaction: 'interaction_data: ',
} as const;

/**
 * Incremental SSE reader.
 *
 * Feed it decoded chunks; it holds a partial trailing line between calls, since
 * a chunk boundary can land mid-line.
 *
 * `data:` lines accumulate and are parsed once at the blank-line delimiter, so
 * an event whose payload spans several lines survives. The `*_data:` prefixes
 * sit outside the SSE field grammar and stay one frame per line.
 */
export class SseParser {
  private buffer = '';
  private stepIndex = 0;
  private eventType = '';
  private dataLines: string[] = [];

  /**
   * @param mediaProxyUrl Rebases `*_url` fields in artifact payloads, so hits
   * carrying a container-local VST host stay playable from the browser.
   */
  constructor(private readonly mediaProxyUrl?: string) {}

  feed(chunk: string): SseEvent[] {
    // Normalise CRLF first: a proxy that rewrites line endings would otherwise
    // leave a stray \r on every payload and break JSON.parse.
    this.buffer += chunk.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    const lines = this.buffer.split('\n');
    // The last element is either an incomplete line or '' — keep it for later.
    this.buffer = lines.pop() ?? '';

    const events: SseEvent[] = [];
    for (const raw of lines) this.readLine(raw.trimEnd(), events);
    return events;
  }

  /** Flush an event the stream ended without a closing blank line. */
  finish(): SseEvent[] {
    const events: SseEvent[] = [];
    const trailing = this.buffer.trimEnd();
    this.buffer = '';
    if (trailing) this.readLine(trailing, events);
    this.dispatchEvent(events);
    return events;
  }

  private readLine(line: string, events: SseEvent[]): void {
    if (!line) {
      this.dispatchEvent(events);
      return;
    }
    if (line.startsWith(':')) return; // keepalive comment

    if (line.startsWith(PREFIXES.event)) {
      // A second `event:` with data still buffered means the delimiter never
      // arrived; the fields read so far belong to the earlier event.
      this.dispatchEvent(events);
      this.eventType = line.slice(PREFIXES.event.length).trim();
      return;
    }

    if (line.startsWith(PREFIXES.data)) {
      this.dataLines.push(line.slice(PREFIXES.data.length).replace(/^ /, ''));
      return;
    }

    if (line.startsWith(PREFIXES.step)) {
      const step = this.parseStep(line.slice(PREFIXES.step.length));
      if (step) events.push({ kind: 'step', step });
      return;
    }

    if (line.startsWith(PREFIXES.artifact)) {
      const envelope = this.parseArtifact(line.slice(PREFIXES.artifact.length));
      if (envelope) events.push({ kind: 'artifact', envelope });
      return;
    }

    if (line.startsWith(PREFIXES.error)) {
      const message = this.parseError(line.slice(PREFIXES.error.length));
      if (message) events.push({ kind: 'error', message });
      return;
    }

    if (line.startsWith(PREFIXES.interaction)) {
      events.push({
        kind: 'error',
        message: 'Interactive agent responses are not supported by this UI.',
      });
    }
  }

  private dispatchEvent(events: SseEvent[]): void {
    const { dataLines, eventType } = this;
    this.dataLines = [];
    this.eventType = '';
    if (!dataLines.length) return;

    const payload = dataLines.join('\n').trim();
    if (eventType === 'interaction_required') {
      const interaction = this.parseInteraction(payload);
      if (interaction) events.push({ kind: 'interaction', interaction });
      return;
    }
    if (payload === '[DONE]') {
      events.push({ kind: 'done' });
      return;
    }
    let text = '';
    try {
      text = extractContent(JSON.parse(payload));
    } catch {
      // Not JSON: some backends stream bare text after `data: `.
      text = payload;
    }
    if (text) events.push({ kind: 'token', text });
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

  /**
   * Structured payload for a feature tab, kept out of the assistant text.
   *
   * The alternative is the agent transcribing every hit into the reply, which
   * costs a large payload of tokens and invites truncated or invented fields.
   */
  private parseArtifact(payload: string): string | null {
    try {
      const parsed: unknown = JSON.parse(payload);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
      return artifactEnvelope(parsed as Record<string, unknown>, this.mediaProxyUrl);
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

  private parseInteraction(payload: string): InteractionRequest | null {
    try {
      const interaction = JSON.parse(payload) as Partial<InteractionRequest>;
      if (
        interaction.event_type !== 'interaction_required' ||
        typeof interaction.execution_id !== 'string' ||
        typeof interaction.interaction_id !== 'string' ||
        typeof interaction.response_url !== 'string' ||
        typeof interaction.prompt?.text !== 'string' ||
        typeof interaction.prompt?.input_type !== 'string'
      ) {
        return null;
      }
      return interaction as InteractionRequest;
    } catch {
      return null;
    }
  }

}

// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/** Browser-side consumer for the versioned VSS agent API contract. */

import type { AgentQuestion, AgentQuestionInteractionRequest, ChatStep } from './types';

const MAX_FRAME_LENGTH = 5_000_000;
const PROTOCOL_MAJOR = '1';
const ARTIFACT_KIND = /^vss\.[a-z0-9]+(?:[._-][a-z0-9]+)*$/;

type JsonObject = Record<string, unknown>;

export interface AgentApiEvent {
  protocol_version: string;
  id: string;
  type: string;
  run_id: string;
  thread_id: string;
  data: JsonObject;
}

export interface AgentApiRun {
  run_id: string;
  events_url: string;
  cancel_url: string;
  /** Present when the agent API exposes mid-run interaction responses. */
  respond_url?: string;
}

export type AgentApiChatEvent =
  | { kind: 'token'; text: string }
  | { kind: 'step'; step: ChatStep }
  | { kind: 'artifact'; envelope: string }
  | { kind: 'interaction'; interaction: AgentQuestionInteractionRequest }
  | { kind: 'interaction-resolved'; interactionId: string; status: string }
  | { kind: 'error'; message: string }
  | { kind: 'done' };

export interface AgentApiChatState {
  reasoning: string;
  toolArguments: Map<string, string>;
}

export const createAgentApiChatState = (): AgentApiChatState => ({
  reasoning: '',
  toolArguments: new Map<string, string>(),
});

export const assertAgentApiEventScope = (
  event: AgentApiEvent,
  runId: string,
  threadId: string,
): void => {
  if (event.run_id !== runId || event.thread_id !== threadId) {
    throw new Error('agent API emitted an event for the wrong run or thread');
  }
};

const asString = (value: unknown): string | undefined =>
  typeof value === 'string' ? value : undefined;

const serialize = (value: unknown): string | undefined => {
  if (typeof value === 'string') return value;
  if (value === undefined || value === null) return undefined;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
};

const safeString = (value: unknown, maximum = 100_000): string | undefined => {
  if (typeof value !== 'string' || !value.trim() || value.length > maximum) return undefined;
  return value;
};

const parseQuestion = (value: unknown): AgentQuestion | null => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const question = value as JsonObject;
  const questionId = safeString(question.question_id, 256);
  const header =
    typeof question.header === 'string' && question.header.length <= 12
      ? question.header
      : undefined;
  const prompt = safeString(question.prompt);
  if (
    !questionId ||
    !/^[a-z][a-z0-9_]*$/.test(questionId) ||
    header === undefined ||
    !prompt ||
    question.secret === true ||
    !Array.isArray(question.options) ||
    question.options.length > 4
  ) {
    return null;
  }
  const options = question.options.map((option) => {
    if (!option || typeof option !== 'object' || Array.isArray(option)) return null;
    const candidate = option as JsonObject;
    const label = safeString(candidate.label);
    const description =
      candidate.description === undefined ? undefined : safeString(candidate.description);
    return label && (candidate.description === undefined || description)
      ? { label, ...(description ? { description } : {}) }
      : null;
  });
  if (options.some((option) => option === null)) return null;
  return {
    question_id: questionId,
    header,
    prompt,
    options: options as AgentQuestion['options'],
    multi_select: question.multi_select === true,
    allow_other: question.allow_other === true,
    secret: false,
  };
};

const parseInteraction = (
  event: AgentApiEvent,
  responseUrl?: string,
): AgentQuestionInteractionRequest | null => {
  const data = event.data;
  const interactionId = safeString(data.interaction_id, 256);
  if (
    data.kind !== 'questions' ||
    !interactionId ||
    !responseUrl ||
    !Array.isArray(data.questions) ||
    data.questions.length < 1 ||
    data.questions.length > 3 ||
    typeof data.created_at_ms !== 'number' ||
    !Number.isSafeInteger(data.created_at_ms) ||
    data.created_at_ms < 0 ||
    typeof data.expires_at_ms !== 'number' ||
    !Number.isSafeInteger(data.expires_at_ms) ||
    data.expires_at_ms <= data.created_at_ms
  ) {
    return null;
  }
  const questions = data.questions.map(parseQuestion);
  if (questions.some((question) => question === null)) return null;
  return {
    event_type: 'interaction_required',
    execution_id: event.run_id,
    interaction_id: interactionId,
    questions: questions as AgentQuestion[],
    response_url: responseUrl,
    created_at_ms: data.created_at_ms,
    expires_at_ms: data.expires_at_ms,
  };
};

const sequence = (event: AgentApiEvent): number => Number.parseInt(event.id, 10) || 0;

const isAgentApiEvent = (value: unknown): value is AgentApiEvent => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const event = value as Partial<AgentApiEvent>;
  return (
    typeof event.protocol_version === 'string' &&
    event.protocol_version.split('.')[0] === PROTOCOL_MAJOR &&
    typeof event.id === 'string' &&
    typeof event.type === 'string' &&
    typeof event.run_id === 'string' &&
    typeof event.thread_id === 'string' &&
    !!event.data &&
    typeof event.data === 'object' &&
    !Array.isArray(event.data)
  );
};

/** Incrementally decode structured events from the agent API's SSE response. */
export class AgentApiSseParser {
  private buffer = '';

  feed(chunk: string): AgentApiEvent[] {
    this.buffer = (this.buffer + chunk).replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    if (this.buffer.length > MAX_FRAME_LENGTH) {
      throw new Error('agent API emitted an oversized SSE frame');
    }
    const frames = this.buffer.split('\n\n');
    this.buffer = frames.pop() ?? '';
    return frames.flatMap((frame) => this.parseFrame(frame));
  }

  finish(): AgentApiEvent[] {
    const frame = this.buffer;
    this.buffer = '';
    return frame ? this.parseFrame(frame) : [];
  }

  private parseFrame(frame: string): AgentApiEvent[] {
    const data = frame
      .split('\n')
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).replace(/^ /, ''))
      .join('\n');
    if (!data || data === '[DONE]') return [];
    let parsed: unknown;
    try {
      parsed = JSON.parse(data);
    } catch {
      throw new Error('agent API emitted invalid SSE JSON');
    }
    if (!isAgentApiEvent(parsed)) {
      throw new Error('agent API emitted an invalid protocol event');
    }
    return [parsed];
  }
}

const proxyArtifactMedia = (value: unknown, mediaProxyUrl?: string, key = ''): unknown => {
  if (key.endsWith('_url') && mediaProxyUrl && typeof value === 'string') {
    try {
      const url = new URL(value);
      if (url.protocol === 'http:' || url.protocol === 'https:') {
        return `${mediaProxyUrl.replace(/\/$/, '')}${url.pathname}${url.search}`;
      }
    } catch {
      return value;
    }
  }
  if (Array.isArray(value)) {
    return value.map((item) => proxyArtifactMedia(item, mediaProxyUrl));
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as JsonObject).map(([childKey, child]) => [
        childKey,
        proxyArtifactMedia(child, mediaProxyUrl, childKey),
      ]),
    );
  }
  return value;
};

/**
 * Wrap a `{version, kind, payload}` frame as the envelope the tab parsers read.
 *
 * Shared with the chat-SSE parser: both transports carry the same artifact
 * contract, and a second copy of this validation would let them drift.
 */
export const artifactEnvelope = (data: JsonObject, mediaProxyUrl?: string): string | null => {
  const version = asString(data.version);
  const kind = asString(data.kind);
  const payload = data.payload;
  if (
    version !== '1.0' ||
    !kind ||
    !ARTIFACT_KIND.test(kind) ||
    !payload ||
    typeof payload !== 'object' ||
    Array.isArray(payload)
  ) {
    return null;
  }
  try {
    const json = JSON.stringify({
      version,
      kind,
      payload: proxyArtifactMedia(payload, mediaProxyUrl),
    }).replace(/</g, '\\u003c');
    return `<vss-ui-artifact>${json}</vss-ui-artifact>`;
  } catch {
    return null;
  }
};

/** Convert one agent API event into updates understood by the replacement chat UI. */
export function agentApiEventToChatEvents(
  event: AgentApiEvent,
  state: AgentApiChatState,
  mediaProxyUrl?: string,
  responseUrl?: string,
): AgentApiChatEvent[] {
  const data = event.data;
  if (event.type === 'message.delta') {
    const text = asString(data.delta);
    return text ? [{ kind: 'token', text }] : [];
  }
  if (event.type === 'run.started' || event.type === 'run.completed') {
    const complete = event.type === 'run.completed';
    return [
      {
        kind: 'step',
        step: {
          id: `run-status-${event.run_id}`,
          name: 'Agent run',
          status: complete ? 'complete' : 'in_progress',
          payload: complete ? 'Agent run completed.' : 'Waiting for the agent backend...',
          index: sequence(event),
        },
      },
      ...(complete ? ([{ kind: 'done' }] as AgentApiChatEvent[]) : []),
    ];
  }
  if (event.type === 'reasoning.delta') {
    state.reasoning += asString(data.delta) ?? '';
    return [
      {
        kind: 'step',
        step: {
          id: `reasoning-${event.run_id}`,
          name: 'Reasoning',
          status: 'in_progress',
          payload: state.reasoning,
          index: sequence(event),
        },
      },
    ];
  }
  if (event.type.startsWith('tool.')) {
    const id = asString(data.tool_call_id) ?? `tool-${event.id}`;
    if (event.type === 'tool.arguments.delta') {
      state.toolArguments.set(
        id,
        (state.toolArguments.get(id) ?? '') + (asString(data.delta) ?? ''),
      );
    } else if (typeof data.arguments === 'string') {
      state.toolArguments.set(id, data.arguments);
    }
    const status: ChatStep['status'] =
      event.type === 'tool.failed'
        ? 'error'
        : event.type === 'tool.completed'
          ? 'complete'
          : 'in_progress';
    return [
      {
        kind: 'step',
        step: {
          id,
          name: asString(data.name) ?? 'Agent tool',
          status,
          payload:
            serialize(data.error) ??
            serialize(data.output) ??
            serialize(data.payload) ??
            state.toolArguments.get(id),
          index: sequence(event),
        },
      },
    ];
  }
  if (event.type === 'artifact.created') {
    const envelope = artifactEnvelope(data, mediaProxyUrl);
    return envelope ? [{ kind: 'artifact', envelope }] : [];
  }
  if (event.type === 'interaction.required') {
    const interaction = parseInteraction(event, responseUrl);
    if (!interaction) throw new Error('agent API emitted an invalid interaction');
    return [{ kind: 'interaction', interaction }];
  }
  if (event.type === 'interaction.resolved') {
    const interactionId = safeString(data.interaction_id, 256);
    const status = safeString(data.status, 32);
    return interactionId && status ? [{ kind: 'interaction-resolved', interactionId, status }] : [];
  }
  if (event.type === 'run.failed') {
    const error = data.error;
    const message =
      error && typeof error === 'object' && !Array.isArray(error)
        ? asString((error as JsonObject).message)
        : undefined;
    return [
      {
        kind: 'error',
        message: message ?? 'The agent backend could not complete this request.',
      },
      { kind: 'done' },
    ];
  }
  if (event.type === 'run.cancelled') {
    return [{ kind: 'error', message: 'cancelled' }, { kind: 'done' }];
  }
  return [];
}

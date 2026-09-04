// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/** Browser-side consumer for the versioned VSS agent API contract. */

import type { ChatStep } from './types';

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
}

export type AgentApiChatEvent =
  | { kind: 'token'; text: string }
  | { kind: 'step'; step: ChatStep }
  | { kind: 'artifact'; envelope: string }
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

export const assertAgentApiEventScope = (event: AgentApiEvent, runId: string, threadId: string): void => {
  if (event.run_id !== runId || event.thread_id !== threadId) {
    throw new Error('agent API emitted an event for the wrong run or thread');
  }
};

const asString = (value: unknown): string | undefined => (typeof value === 'string' ? value : undefined);

const serialize = (value: unknown): string | undefined => {
  if (typeof value === 'string') return value;
  if (value === undefined || value === null) return undefined;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
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

const artifactEnvelope = (data: JsonObject, mediaProxyUrl?: string): string | null => {
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
      state.toolArguments.set(id, (state.toolArguments.get(id) ?? '') + (asString(data.delta) ?? ''));
    } else if (typeof data.arguments === 'string') {
      state.toolArguments.set(id, data.arguments);
    }
    const status: ChatStep['status'] =
      event.type === 'tool.failed' ? 'error' : event.type === 'tool.completed' ? 'complete' : 'in_progress';
    return [
      {
        kind: 'step',
        step: {
          id,
          name: asString(data.name) ?? 'Agent tool',
          status,
          payload:
            serialize(data.error) ?? serialize(data.output) ?? serialize(data.payload) ?? state.toolArguments.get(id),
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
    return [
      {
        kind: 'error',
        message: 'Interactive agent responses are not supported by this UI.',
      },
    ];
  }
  if (event.type === 'run.failed') {
    const error = data.error;
    const message =
      error && typeof error === 'object' && !Array.isArray(error) ? asString((error as JsonObject).message) : undefined;
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

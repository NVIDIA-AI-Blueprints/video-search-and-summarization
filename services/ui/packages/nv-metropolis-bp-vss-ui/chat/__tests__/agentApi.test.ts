// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import {
  assertAgentApiEventScope,
  createAgentApiChatState,
  agentApiEventToChatEvents,
  AgentApiSseParser,
  type AgentApiEvent,
} from '../lib-src/agentApi';

const event = (type: string, data: Record<string, unknown> = {}, id = '1'): AgentApiEvent => ({
  protocol_version: '1.0',
  id,
  type,
  run_id: 'run_1',
  thread_id: 'thread_1',
  data,
});

describe('AgentApiSseParser', () => {
  it('decodes a frame split across network chunks', () => {
    const parser = new AgentApiSseParser();
    const frame = `id: 1\nevent: message.delta\ndata: ${JSON.stringify(
      event('message.delta', { delta: 'hello' }),
    )}\n\n`;
    expect(parser.feed(frame.slice(0, 21))).toEqual([]);
    expect(parser.feed(frame.slice(21))).toEqual([event('message.delta', { delta: 'hello' })]);
  });

  it('handles a CRLF frame delimiter split across chunks', () => {
    const parser = new AgentApiSseParser();
    const frame = `data: ${JSON.stringify(event('run.completed'))}`;
    expect(parser.feed(`${frame}\r`)).toEqual([]);
    expect(parser.feed('\n\r\n')).toEqual([event('run.completed')]);
  });

  it('rejects incompatible protocol events', () => {
    const parser = new AgentApiSseParser();
    const incompatible = { ...event('run.started'), protocol_version: '2.0' };
    expect(() => parser.feed(`data: ${JSON.stringify(incompatible)}\n\n`)).toThrow('invalid protocol event');
  });

  it('rejects events belonging to another run or thread', () => {
    expect(() => assertAgentApiEventScope(event('run.started'), 'run_2', 'thread_1')).toThrow('wrong run or thread');
    expect(() => assertAgentApiEventScope(event('run.started'), 'run_1', 'thread_2')).toThrow('wrong run or thread');
  });
});

describe('agentApiEventToChatEvents', () => {
  it('updates one tool row as arguments stream and the call completes', () => {
    const state = createAgentApiChatState();
    const started = agentApiEventToChatEvents(
      event('tool.arguments.delta', {
        tool_call_id: 'tool_1',
        name: 'vss_search',
        delta: '{"query":',
      }),
      state,
    );
    const completed = agentApiEventToChatEvents(
      event('tool.completed', { tool_call_id: 'tool_1', name: 'vss_search', output: '3 results' }, '2'),
      state,
    );

    expect(started[0]).toMatchObject({
      kind: 'step',
      step: { id: 'tool_1', status: 'in_progress', payload: '{"query":' },
    });
    expect(completed[0]).toMatchObject({
      kind: 'step',
      step: { id: 'tool_1', status: 'complete', payload: '3 results' },
    });
  });

  it('preserves validated artifacts for feature-tab subscribers', () => {
    const updates = agentApiEventToChatEvents(
      event('artifact.created', {
        version: '1.0',
        kind: 'vss.search.results',
        payload: {
          data: [
            {
              video_name: 'clip.mp4',
              video_url: 'http://vss.local/vst/clip.mp4',
            },
          ],
        },
      }),
      createAgentApiChatState(),
      '/api/proxy',
    );

    expect(updates).toHaveLength(1);
    expect(updates[0]).toMatchObject({ kind: 'artifact' });
    expect((updates[0] as { envelope: string }).envelope).toContain('<vss-ui-artifact>');
    expect((updates[0] as { envelope: string }).envelope).toContain('/api/proxy/vst/clip.mp4');
  });

  it('does not advertise a response UI for unsupported interactions', () => {
    expect(
      agentApiEventToChatEvents(
        event('interaction.required', { interaction_id: 'interaction_1' }),
        createAgentApiChatState(),
      ),
    ).toEqual([
      {
        kind: 'error',
        message: 'Interactive agent responses are not supported by this UI.',
      },
    ]);
  });
});

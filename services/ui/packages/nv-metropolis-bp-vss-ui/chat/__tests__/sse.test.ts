// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { SseParser, extractContent } from '../lib-src/sse';

describe('extractContent', () => {
  it('reads OpenAI streaming deltas', () => {
    expect(extractContent({ choices: [{ delta: { content: 'hi' } }] })).toBe('hi');
  });

  it('reads non-streaming message content', () => {
    expect(extractContent({ choices: [{ message: { content: 'hi' } }] })).toBe('hi');
  });

  it('reads the plain field shapes some agent servers return', () => {
    expect(extractContent({ value: 'a' })).toBe('a');
    expect(extractContent({ output: 'b' })).toBe('b');
    expect(extractContent({ answer: 'c' })).toBe('c');
  });

  it('returns empty rather than throwing on unknown shapes', () => {
    expect(extractContent({ nothing: 1 })).toBe('');
    expect(extractContent(null)).toBe('');
  });
});

describe('SseParser', () => {
  it('emits tokens and a terminal done', () => {
    const p = new SseParser();
    const events = p.feed(
      'data: {"choices":[{"delta":{"content":"He"}}]}\n\n' +
        'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n' +
        'data: [DONE]\n\n',
    );
    expect(events).toEqual([
      { kind: 'token', text: 'He' },
      { kind: 'token', text: 'llo' },
      { kind: 'done' },
    ]);
  });

  it('ignores keepalive comments', () => {
    const p = new SseParser();
    expect(p.feed(': keepalive\n\n')).toEqual([]);
  });

  it('handles a frame split across chunk boundaries', () => {
    const p = new SseParser();
    // Chunk ends mid-JSON; nothing should be emitted until it completes.
    expect(p.feed('data: {"choices":[{"delta":{"cont')).toEqual([]);
    expect(p.feed('ent":"split"}}]}\n\n')).toEqual([{ kind: 'token', text: 'split' }]);
  });

  it('parses NAT interaction_required event frames', () => {
    const p = new SseParser();
    const interaction = {
      event_type: 'interaction_required',
      execution_id: 'execution-1',
      interaction_id: 'interaction-1',
      prompt: {
        text: 'Choose a scenario',
        input_type: 'text',
        placeholder: 'warehouse monitoring',
        required: true,
      },
      response_url: '/executions/execution-1/interactions/interaction-1/response',
    };

    expect(
      p.feed(`event: interaction_required\ndata: ${JSON.stringify(interaction)}\n\n`),
    ).toEqual([{ kind: 'interaction', interaction }]);
  });

  it('assembles an interaction whose payload spans several data lines', () => {
    const p = new SseParser();
    const interaction = {
      event_type: 'interaction_required',
      execution_id: 'execution-2',
      interaction_id: 'interaction-2',
      prompt: { text: 'Choose a scenario', input_type: 'text' },
      response_url: '/executions/execution-2/interactions/interaction-2/response',
    };
    const body = JSON.stringify(interaction, null, 2)
      .split('\n')
      .map((line) => `data: ${line}`)
      .join('\n');

    expect(p.feed(`event: interaction_required\n${body}\n\n`)).toEqual([
      { kind: 'interaction', interaction },
    ]);
  });

  it('joins multi-line token payloads with newlines', () => {
    const p = new SseParser();
    expect(p.feed('data: first\ndata: second\n\n')).toEqual([
      { kind: 'token', text: 'first\nsecond' },
    ]);
  });

  it('holds an event until the blank-line delimiter', () => {
    const p = new SseParser();
    expect(p.feed('data: {"choices":[{"delta":{"content":"held"}}]}\n')).toEqual([]);
    expect(p.feed('\n')).toEqual([{ kind: 'token', text: 'held' }]);
  });

  it('dispatches an event the stream ended without a blank line', () => {
    const p = new SseParser();
    expect(p.feed('data: [DONE]')).toEqual([]);
    expect(p.finish()).toEqual([{ kind: 'done' }]);
  });

  it('drops a malformed multi-line interaction without emitting', () => {
    const p = new SseParser();
    expect(p.feed('event: interaction_required\ndata: {\ndata:   "prompt":\ndata: \n\n')).toEqual(
      [],
    );
    expect(p.feed('data: [DONE]\n\n')).toEqual([{ kind: 'done' }]);
  });

  it('parses tool steps and numbers them in order', () => {
    const p = new SseParser();
    const events = p.feed(
      'intermediate_data: {"id":"1","name":"read","status":"in_progress","payload":"x"}\n' +
        'intermediate_data: {"id":"2","name":"exec","status":"complete"}\n',
    );
    expect(events).toEqual([
      { kind: 'step', step: { id: '1', name: 'read', status: 'in_progress', payload: 'x', index: 0 } },
      { kind: 'step', step: { id: '2', name: 'exec', status: 'complete', payload: undefined, index: 1 } },
    ]);
  });

  it('falls back to bare text when a data frame is not JSON', () => {
    const p = new SseParser();
    expect(p.feed('data: plain text\n\n')).toEqual([{ kind: 'token', text: 'plain text' }]);
  });

  it('drops malformed step payloads instead of throwing', () => {
    const p = new SseParser();
    expect(p.feed('intermediate_data: {not json\n')).toEqual([]);
  });
});

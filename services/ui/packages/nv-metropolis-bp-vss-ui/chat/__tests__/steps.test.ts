// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { SseParser, buildStepTree } from '../lib-src/sse';
import { buildContextPrefix } from '../lib-src/useChatStream';
import type { ChatStep } from '../lib-src/types';

const step = (id: string, parentId?: string): ChatStep => ({
  id,
  name: id,
  status: 'complete',
  index: 0,
  parentId,
});

describe('buildStepTree', () => {
  it('nests children under their parent', () => {
    const tree = buildStepTree([step('a'), step('b', 'a'), step('c', 'b')]);
    expect(tree).toHaveLength(1);
    expect(tree[0].children![0].id).toBe('b');
    expect(tree[0].children![0].children![0].id).toBe('c');
  });

  it('keeps a child that arrived before its parent', () => {
    const tree = buildStepTree([step('b', 'a'), step('a')]);
    expect(tree.map((s) => s.id)).toEqual(['a']);
    expect(tree[0].children!.map((s) => s.id)).toEqual(['b']);
  });

  it('shows an orphan at the root rather than dropping it', () => {
    const tree = buildStepTree([step('b', 'missing')]);
    expect(tree.map((s) => s.id)).toEqual(['b']);
  });

  it('does not let a step parent itself into an infinite tree', () => {
    const tree = buildStepTree([step('a', 'a')]);
    expect(tree.map((s) => s.id)).toEqual(['a']);
    expect(tree[0].children).toEqual([]);
  });
});

describe('SseParser extras', () => {
  it('reads parent_id off a step frame', () => {
    const events = new SseParser().feed(
      'intermediate_data: {"id":"2","name":"search","parent_id":"1"}\n',
    );
    expect(events[0]).toMatchObject({ kind: 'step', step: { id: '2', parentId: '1' } });
  });

  it('surfaces an error frame', () => {
    const events = new SseParser().feed('error_data: {"message":"tool exploded"}\n');
    expect(events[0]).toEqual({ kind: 'error', message: 'tool exploded' });
  });

  it('reports an interaction frame as unsupported', () => {
    const events = new SseParser().feed(
      'interaction_data: {"id":"i1","content":{"input_type":"oauth_consent","oauth_url":"https://idp/x","text":"Sign in"}}\n',
    );
    expect(events[0]).toEqual({
      kind: 'error',
      message: 'Interactive agent responses are not supported by this UI.',
    });
  });

  it('survives CRLF line endings from a rewriting proxy', () => {
    const events = new SseParser().feed(
      'data: {"choices":[{"delta":{"content":"hi"}}]}\r\n\r\ndata: [DONE]\r\n\r\n',
    );
    expect(events).toEqual([{ kind: 'token', text: 'hi' }, { kind: 'done' }]);
  });
});

describe('buildContextPrefix', () => {
  it('sends only the data payload, never the UI fields', () => {
    const prefix = buildContextPrefix([
      { id: 'chip1', label: 'Camera 3', contextType: 'media/video', data: { videoId: 'v3' } },
    ]);
    expect(prefix).toBe('[Context: [{"videoId":"v3"}]]');
    expect(prefix).not.toContain('chip1');
    expect(prefix).not.toContain('Camera 3');
  });

  it('strips contextType even when duplicated inside data', () => {
    const prefix = buildContextPrefix([
      {
        id: 'a',
        label: 'a',
        contextType: 'media/video',
        data: { contextType: 'media/video', videoId: 'v1' },
      },
    ]);
    expect(prefix).toBe('[Context: [{"videoId":"v1"}]]');
  });

  it('is empty with no chips, so nothing is prepended', () => {
    expect(buildContextPrefix([])).toBe('');
  });
});

// SPDX-License-Identifier: MIT
import {
  buildExport,
  createConversation,
  filterConversations,
  parseImport,
  sanitizeForPersistence,
  titleFromMessage,
} from '../lib-src/conversations';
import type { Conversation } from '../lib-src/types';

const conversation = (name: string, texts: string[]): Conversation => ({
  id: name,
  name,
  messages: texts.map((content, i) => ({ id: `${name}-${i}`, role: 'user' as const, content })),
});

describe('titleFromMessage', () => {
  it('truncates at 30 characters, as the toolkit did', () => {
    expect(titleFromMessage('a'.repeat(40))).toBe(`${'a'.repeat(30)}...`);
  });

  it('falls back for an empty message', () => {
    expect(titleFromMessage('   ')).toBe('New Conversation');
  });
});

describe('filterConversations', () => {
  const list = [conversation('Cameras', ['show me the loading dock']), conversation('Alerts', [])];

  it('matches on name and on message text', () => {
    expect(filterConversations(list, 'camera').map((c) => c.name)).toEqual(['Cameras']);
    expect(filterConversations(list, 'loading dock').map((c) => c.name)).toEqual(['Cameras']);
  });

  it('returns everything for an empty term', () => {
    expect(filterConversations(list, '  ')).toHaveLength(2);
  });

  it('ignores hidden messages so upload auto-prompts are not searchable', () => {
    const withHidden: Conversation[] = [
      {
        id: 'x',
        name: 'X',
        messages: [{ id: 'm', role: 'user', content: 'secret prompt', hidden: true }],
      },
    ];
    expect(filterConversations(withHidden, 'secret')).toHaveLength(0);
  });
});

describe('sanitizeForPersistence', () => {
  it('drops streaming state so a reload does not restore a stuck cursor', () => {
    const [saved] = sanitizeForPersistence([
      {
        id: 'c',
        name: 'c',
        messages: [
          { id: 'm', role: 'assistant', content: 'partial', streaming: true, uploadConversationId: 'c' },
        ],
      },
    ]);
    expect(saved.messages[0]).not.toHaveProperty('streaming');
    expect(saved.messages[0]).not.toHaveProperty('uploadConversationId');
    expect(saved.messages[0].content).toBe('partial');
  });
});

describe('parseImport', () => {
  it('round-trips this package’s own export', () => {
    const original = [conversation('Trip', ['hello'])];
    const result = parseImport(JSON.stringify(buildExport(original)));
    expect(result.conversations).toHaveLength(1);
    expect(result.conversations![0].name).toBe('Trip');
    expect(result.conversations![0].messages[0].content).toBe('hello');
  });

  it('accepts the toolkit v4 envelope', () => {
    const raw = JSON.stringify({
      version: 4,
      history: [{ id: 'a', name: 'Old chat', messages: [{ role: 'user', content: 'hi' }] }],
      folders: [],
      prompts: [],
    });
    expect(parseImport(raw).conversations![0].name).toBe('Old chat');
  });

  it('accepts a v1 bare array', () => {
    const raw = JSON.stringify([{ id: 1, name: 'V1', messages: [{ role: 'user', content: 'hi' }] }]);
    expect(parseImport(raw).conversations![0].name).toBe('V1');
  });

  it('strips prototype-pollution keys instead of importing them', () => {
    const raw =
      '{"version":4,"history":[{"id":"a","name":"n","messages":[],"__proto__":{"polluted":true}}],"folders":[],"prompts":[]}';
    expect(parseImport(raw).conversations).toHaveLength(1);
    expect(({} as any).polluted).toBeUndefined();
  });

  it('reports rather than throws on malformed input', () => {
    expect(parseImport('not json').error).toBe('Invalid JSON format');
    expect(parseImport('{"version":4}').error).toBe('Invalid import format');
    expect(parseImport('').error).toBe('Empty import file');
  });

  it('rejects a file large enough to be a denial of service', () => {
    expect(parseImport('x'.repeat(11 * 1024 * 1024)).error).toMatch(/too large/);
  });
});

describe('createConversation', () => {
  it('gives every conversation a distinct id', () => {
    const ids = new Set(Array.from({ length: 50 }, () => createConversation().id));
    expect(ids.size).toBe(50);
  });
});

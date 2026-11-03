// SPDX-License-Identifier: Apache-2.0

// The box tool in Edit mode: reading the describe and track routes, extending and ending a track, and the draft item it
// produces (contract: reviewer box -> AI describe -> track).

import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { register } from 'node:module';
import test from 'node:test';

register('./support/alias-loader.mjs', import.meta.url);

const tool = await import('../src/lib/evidence/box-tool.ts');
const { boxToolClient } = await import('../src/lib/evidence/box-tool-client.ts');
const { editFromDraft } = await import('../src/modules/reports/domain/edit-draft.ts');
const sections = new URL('../src/sections/', import.meta.url);

const sample = (t, y = 100) => ({ t, box: [y, 200, y + 200, 400], conf: 0.9 });
const track = (ts, extra = {}) => ({
  source: 'reviewer',
  samples: ts.map((t) => sample(t)),
  span: [ts[0], ts[ts.length - 1]],
  partial: false,
  lostAt: null,
  ...extra,
});
const emptyDraft = () => ({
  incident: { type: 'assault', start_timestamp: 2, end_timestamp: 9, description: '', severity_level: 3 },
  entities: [],
  instruments: [],
  assets: [],
});
const item = (extra = {}) => ({
  itemType: 'asset',
  name: 'Black duffel bag',
  description: 'A black bag on the floor.',
  aiProposal: null,
  track: track([3, 4, 5, 6]),
  ...extra,
});

test('extending back or on keeps the old samples, adds only the new side and takes the latest run', () => {
  const base = track([3, 4, 5, 6]);
  const back = tool.extendTrack(base, track([0, 1, 2, 3, 4]), 'back');
  assert.deepEqual(
    back.samples.map((s) => s.t),
    [0, 1, 2, 3, 4, 5, 6],
  );
  assert.deepEqual(back.span, [0, 6]);
  const on = tool.extendTrack(base, track([6, 7, 8, 9], { partial: true, lostAt: 9 }), 'on');
  assert.deepEqual(
    on.samples.map((s) => s.t),
    [3, 4, 5, 6, 7, 8, 9],
  );
  assert.equal(on.partial, true, 'a run that lost the item makes the track incomplete');
});

test('an extension is seeded at the edge sample and reaches only one side', () => {
  const base = track([3, 4, 5, 6]);
  assert.deepEqual(tool.extensionRequest(base, 'back'), { seconds: 3, box: base.samples[0].box, backSeconds: 3, forwardSeconds: 0 });
  assert.deepEqual(tool.extensionRequest(base, 'on'), { seconds: 6, box: base.samples[3].box, backSeconds: 0, forwardSeconds: 3 });
});

test('"Gone from here on" drops later samples and clears the incomplete mark; with nothing left it gives none', () => {
  const ended = tool.endTrackAt(track([3, 4, 5, 6], { partial: true, lostAt: 6 }), 4);
  assert.deepEqual(
    ended.samples.map((s) => s.t),
    [3, 4],
  );
  assert.deepEqual([ended.partial, ended.lostAt], [false, null]);
  assert.equal(tool.endTrackAt(track([3, 4]), 1), null);
});

test('adding an asset puts the draft item, its first-seen image and the box item in the draft', () => {
  const next = tool.addBoxItem(emptyDraft(), item(), null);
  assert.equal(next.assets.length, 1);
  const [asset] = next.assets;
  assert.deepEqual([asset.name, asset.description], ['Black duffel bag', 'A black bag on the floor.']);
  assert.equal(asset.timestampSeconds, 3, 'visible from the first-seen second');
  assert.equal(asset.reviewer.track.source, 'reviewer');
  assert.equal(asset.sourceId, null);
  assert.deepEqual(
    editFromDraft(next).assets[0].reviewer.track.samples[0].t,
    3,
    'Save sends the track, which the server cuts the image from',
  );
});

test('adding an entity names it, spans the track and keeps its most visible second inside', () => {
  const next = tool.addBoxItem(emptyDraft(), item({ itemType: 'entity', name: 'Woman in red coat', track: track([2.4, 3, 4.6]) }), null);
  const [entity] = next.entities;
  assert.equal(entity.label, 'Woman in red coat');
  assert.deepEqual([entity.firstSeenSeconds, entity.lastSeenSeconds, entity.timestampSeconds], [2, 5, 2]);
  assert.doesNotThrow(() => editFromDraft(next), 'the draft is a complete edit');
});

test('adding an instrument keeps its holder and threat, and a still image sits at its own second', () => {
  const holder = tool.addBoxItem(emptyDraft(), item({ itemType: 'entity', name: 'Person 1' }), null);
  const key = holder.entities[0].key;
  const next = tool.addBoxItem(
    holder,
    item({
      itemType: 'instrument',
      name: 'Knife',
      threatLevel: 4,
      holder: 'Person 1',
      track: null,
      stillSeconds: 7,
      stillBox: [1, 2, 30, 40],
    }),
    key,
  );
  const [knife] = next.instruments;
  assert.deepEqual([knife.holderKey, knife.threat_level, knife.timestampSeconds, knife.reviewer.stillSeconds], [key, 4, 7, 7]);
  assert.deepEqual(tool.holderChoices(next), [{ key, name: 'Person 1' }], 'holders are offered by name');
});

test('re-tracking an existing item changes its evidence second, box and track but not its text', () => {
  const base = tool.addBoxItem(emptyDraft(), item({ itemType: 'entity', name: 'Person 1', description: 'Man in a grey hoodie' }), null);
  const key = base.entities[0].key;
  const next = tool.retrackDraftItem(
    base,
    'entity',
    key,
    item({ itemType: 'entity', name: 'Person 1', description: 'ignored', track: track([5, 6, 7]) }),
  );
  const [entity] = next.entities;
  assert.deepEqual([entity.label, entity.description], ['Person 1', 'Man in a grey hoodie']);
  assert.deepEqual([entity.timestampSeconds, entity.imageTimestampSeconds, entity.firstSeenSeconds, entity.lastSeenSeconds], [5, 5, 5, 7]);
  assert.equal(entity.key, key);
});

test('the client posts to the proxy routes and reads their answers', async () => {
  const calls = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    calls.push([url, JSON.parse(init.body)]);
    return Response.json(url.endsWith('/describe') ? { proposal: null, reason: 'dark' } : { track: track([1, 2]) });
  };
  try {
    assert.deepEqual(await boxToolClient.describe('vid 1', { seconds: 2, box: [1, 2, 3, 4] }), { proposal: null, reason: 'dark' });
    const read = await boxToolClient.track('vid 1', {
      seconds: 2,
      box: [1, 2, 3, 4],
      itemType: 'asset',
      name: 'Bag',
      description: 'A bag',
    });
    assert.deepEqual(read.span, [1, 2]);
    assert.equal(calls[0][0], '/api/reports/vid%201/box/describe');
    assert.equal(calls[1][0], '/api/reports/vid%201/box/track');
    globalThis.fetch = async () => Response.json({ error: 'agent down' }, { status: 502 });
    await assert.rejects(
      boxToolClient.track('v', { seconds: 1, box: [1, 2, 3, 4], itemType: 'asset', name: 'a', description: 'b' }),
      /agent down/,
    );
  } finally {
    globalThis.fetch = realFetch;
  }
});

test('Edit mode offers + Add evidence and one Edit per card, and no Replace image or crop picker', async () => {
  const pane = await readFile(new URL('reports/components/ReportEditPane/ReportEditPane.tsx', sections), 'utf8');
  assert.match(pane, /\+ Add evidence/);
  assert.match(pane, /onClick=\{onEdit\}[\s\S]*?>\s*Edit\s*</);
  assert.doesNotMatch(pane, /Replace image|EvidenceFramePicker|Track from my box/i);
  assert.match(pane, /<BoxTool/);
  const dialog = await readFile(new URL('evidence/components/BoxTool/BoxTool.tsx', sections), 'utf8');
  for (const text of [
    'Analyse this box',
    'Add to draft',
    'Keep as incomplete track',
    'Keep as still image',
    'Gone from here on',
    'Extend ',
  ]) {
    assert.ok(dialog.includes(text), `the dialog says "${text}"`);
  }
  assert.doesNotMatch(dialog, /\bT-\d|\bE\d\b/, 'track labels use the item name, not an id');
});

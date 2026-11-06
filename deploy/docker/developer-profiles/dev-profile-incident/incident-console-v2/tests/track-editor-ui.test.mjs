// SPDX-License-Identifier: Apache-2.0

// The track editor's wiring: where its starting track comes from, and that the dialog, timeline and Edit mode card hold
// together as the spec says. Source-reading checks, as the rest of the console's component tests.

import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { register } from 'node:module';
import { test } from 'node:test';

register('./support/alias-loader.mjs', import.meta.url);

const { trackEditorSource } = await import('../src/lib/evidence/track-editor-source.ts');
const read = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8');

const saved = (over = {}) => ({ image: 'img', trackUrl: null, located: true, boxSeconds: 4, boundingBox: [100, 100, 300, 300], ...over });
const item = (over = {}) => ({ reviewer: null, image: 'img', ...over });

test('a reviewer track in the draft is edited as it is', () => {
  const samples = [{ t: 1, box: [1, 2, 3, 4] }];
  const source = trackEditorSource(item({ reviewer: { track: { samples } } }), saved({ trackUrl: '/api/t' }));
  assert.deepEqual(source, { samples, trackUrl: null, still: null, badge: 'Your track' });
});

test('a saved AI track is read from its track route', () => {
  assert.deepEqual(trackEditorSource(item(), saved({ trackUrl: '/api/t' })), {
    samples: null,
    trackUrl: '/api/t',
    still: null,
    badge: 'AI track',
  });
});

test('an item with only a located box starts from that still box; an unlocated one from nothing', () => {
  assert.deepEqual(trackEditorSource(item(), saved()).still, { t: 4, box: [100, 100, 300, 300] });
  assert.equal(trackEditorSource(item(), saved()).badge, 'Still image');
  const none = trackEditorSource(item(), saved({ located: false }));
  assert.deepEqual([none.still, none.badge], [null, 'No track']);
  assert.equal(trackEditorSource(item(), saved({ boxSeconds: null })).still, null);
  assert.equal(trackEditorSource(item(), saved({ boundingBox: [0, 0, 0, 0] })).still, null);
});

test('a reviewer still image keeps its box and second', () => {
  const source = trackEditorSource(item({ reviewer: { track: null, stillSeconds: 7, stillBox: [10, 10, 200, 200] } }), null);
  assert.deepEqual([source.still, source.badge], [{ t: 7, box: [10, 10, 200, 200] }, 'Still image']);
});

test('a replaced image no longer has the saved track behind it', () => {
  const source = trackEditorSource(item({ image: 'other' }), saved({ trackUrl: '/api/t' }));
  assert.deepEqual([source.trackUrl, source.badge], [null, 'No track']);
  assert.equal(trackEditorSource(item(), null).badge, 'No track');
});

test('Edit mode cards offer Edit track, and Done goes through the draft re-track path', async () => {
  const pane = await read('sections/reports/components/ReportEditPane/ReportEditPane.tsx');
  assert.match(pane, /data-testid="edit-track"/);
  assert.match(pane, /Edit track/);
  assert.match(pane, /<TrackEditor/);
  assert.match(pane, /retrackDraftItem\(draft, kind, target\.key,/);
  assert.match(pane, /aiProposal: null,\s*track,/);
});

test('track editor presents its heading, item and metadata as separate levels', async () => {
  const editor = await readFile(new URL('../src/sections/evidence/components/TrackEditor/TrackEditor.tsx', import.meta.url), 'utf8');
  assert.match(editor, /text-lg font-bold tracking-tight/);
  assert.match(editor, /aria-label="Track details"/);
  assert.match(editor, /border-b border-border\/70 bg-muted\/40 px-6 py-5/);
  assert.match(editor, /data-testid="track-item-title"/);
  assert.match(editor, /\{itemId && name && ' · '\}/);
  assert.match(editor, /Move the box at the playhead to add a keyframe/);
  assert.doesNotMatch(editor, /flex flex-row flex-wrap items-center gap-2 space-y-0/);
});

test('the dialog follows the console: shadcn Dialog, muted video, a pill Done, Undo and Cancel', async () => {
  const editor = await read('sections/evidence/components/TrackEditor/TrackEditor.tsx');
  assert.match(editor, /from '@\/components\/ui\/dialog'/);
  assert.match(editor, /<MutedVideo/);
  assert.match(editor, /rounded-2xl bg-white p-0 shadow-xl/);
  for (const label of ['Undo', 'Cancel', 'Done']) assert.match(editor, new RegExp(`>\\s*${label}\\s*<`));
  assert.match(editor, /toast\.info\(NEW_TRACK_HINT\)/);
  assert.match(editor, /Track started\. Drag the grips to set where it starts and stops\./);
});

test('Done expands the keyframes with densify and needs an edit and a track', async () => {
  const editor = await read('sections/evidence/components/TrackEditor/TrackEditor.tsx');
  assert.match(editor, /const dense = densify\(track\)/);
  assert.match(editor, /source: 'reviewer'[^}]*partial: false, lostAt: null/);
  assert.match(editor, /canDone = track !== null && edits > 0/);
});

test('every shortcut in the spec is wired and listed', async () => {
  const editor = await read('sections/evidence/components/TrackEditor/TrackEditor.tsx');
  for (const key of ["' '", "'ArrowLeft'", "'k'", "'i'", "'o'", "'Delete'", "'Backspace'", "'?'"])
    assert.ok(editor.includes(`case ${key}:`), key);
  assert.match(editor, /event\.key\.toLowerCase\(\) === 'z'/);
  assert.match(editor, /onEscapeKeyDown/);
  for (const text of ['Play or pause', 'Add a keyframe at the playhead', 'Undo', 'Close help, else clear the selection']) {
    assert.ok(editor.includes(text), text);
  }
});

test('the timeline gives keyboard users the same edits: keyframes and grips are buttons you can nudge', async () => {
  const timeline = await read('sections/evidence/components/TrackEditor/TrackTimeline.tsx');
  assert.match(timeline, /aria-label=\{`Keyframe at /);
  assert.match(timeline, /aria-label=\{`\$\{which === 'start' \? 'Start' : 'Stop'\} of the track/);
  assert.match(timeline, /event\.key === 'ArrowLeft'/);
  assert.match(timeline, /'Remove track' : 'Delete'/);
  assert.match(timeline, /data-testid="track-playhead"/);
});

test('the frame shows a chip per state, a veil outside the track, and draws only where there is no box', async () => {
  const frame = await read('sections/evidence/components/TrackEditor/TrackFrame.tsx');
  assert.match(frame, /STATE_CHIP\[at\.state\]/);
  assert.match(frame, /at\.state === 'outside'/);
  assert.match(frame, /bg-black\/50/);
  assert.match(frame, /drawsBox\(at\.state\)/);
  assert.match(frame, /isUsableBox\(box\)/);
});

test('toasts have a place to show', async () => {
  const layout = await read('app/layout.tsx');
  assert.match(layout, /<Toaster position="top-center" \/>/);
});

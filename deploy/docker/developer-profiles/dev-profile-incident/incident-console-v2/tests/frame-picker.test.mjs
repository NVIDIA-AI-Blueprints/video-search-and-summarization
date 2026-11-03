// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict';
import { register } from 'node:module';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

register('./support/alias-loader.mjs', import.meta.url);

const { boxRegion } = await import('../src/lib/evidence/frame-geometry.ts');
const { dragRegion, pickSeconds, regionBox, usableRange } = await import('../src/lib/evidence/frame-picker.ts');
const { parseRegion, ITEM_ID } = await import('../src/lib/evidence/frame-request.ts');

test('a drag in either direction becomes one rectangle clipped to the frame', () => {
  assert.deepEqual(dragRegion({ x: 0.25, y: 0.125 }, { x: 0.75, y: 0.375 }), { x: 0.25, y: 0.125, w: 0.5, h: 0.25 });
  assert.deepEqual(
    dragRegion({ x: 0.75, y: 0.375 }, { x: 0.25, y: 0.125 }),
    { x: 0.25, y: 0.125, w: 0.5, h: 0.25 },
    'dragging up and left gives the same rectangle',
  );
  assert.deepEqual(
    dragRegion({ x: -0.3, y: 0.5 }, { x: 0.4, y: 1.4 }),
    { x: 0, y: 0.5, w: 0.4, h: 0.5 },
    'a drag past the frame edge is clipped',
  );
});

test('a stray click or a sliver under the crop minimum gives no region', () => {
  assert.equal(dragRegion({ x: 0.5, y: 0.5 }, { x: 0.5, y: 0.5 }), null);
  assert.equal(dragRegion({ x: 0.5, y: 0.2 }, { x: 0.505, y: 0.6 }), null, 'a width under 1% is refused');
});

test('a region becomes the stored [ymin, xmin, ymax, xmax] box on the 0-1000 grid and reads back the same', () => {
  const region = dragRegion({ x: 0.25, y: 0.125 }, { x: 0.75, y: 0.375 });
  assert.deepEqual(regionBox(region), [125, 250, 375, 750]);
  assert.deepEqual(boxRegion(regionBox(region)), region);
});

test('the drawn region passes the crop route rules, so the server accepts what the picker sends', () => {
  const region = dragRegion({ x: 0.75, y: 0.5 }, { x: 0.25, y: 0.125 });
  assert.deepEqual(parseRegion(region), region);
  assert.ok(ITEM_ID.test('entity-m1abc-3'), 'draft keys are valid item IDs');
  assert.ok(ITEM_ID.test('instrument-m2-12'));
});

test('a picked second is never negative and never past the end of the video', () => {
  assert.equal(pickSeconds(-2, 60), 0);
  assert.equal(pickSeconds(72.4, 60), 60);
  assert.equal(pickSeconds(12.5, 60), 12.5);
  assert.equal(pickSeconds(12.5, null), 12.5, 'an unknown length leaves only the lower bound');
});

test('a picked second stays inside the seconds an entity is on screen', () => {
  const seen = { first: 5, last: 10 };
  assert.equal(pickSeconds(0, 60, seen), 5, 'a frame before the entity appears moves to its first second');
  assert.equal(pickSeconds(7, 60, seen), 7);
  assert.equal(pickSeconds(12, 60, seen), 10, 'a frame after the entity leaves moves to its last second');
  assert.equal(pickSeconds(12, null, seen), 10, 'the range still applies when the length is unknown');
});

test('an entity range of 0 to 0 is no range: the picked second may use the whole video', () => {
  assert.equal(usableRange({ first: 0, last: 0 }), null);
  assert.deepEqual(usableRange({ first: 0, last: 4 }), { first: 0, last: 4 });
  assert.deepEqual(usableRange({ first: 3, last: 3 }), { first: 3, last: 3 }, 'a single real second still pins the frame');
  assert.equal(usableRange(null), null);
  assert.equal(pickSeconds(12.5, 60, { first: 0, last: 0 }), 12.5);
  assert.equal(pickSeconds(72, 60, { first: 0, last: 0 }), 60, 'the end of the video still caps it');
});

test('Edit mode has no Replace image and no frame picker', async () => {
  const pane = await readFile(new URL('../src/sections/reports/components/ReportEditPane/ReportEditPane.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(pane, /Replace image|EvidenceFramePicker|onReplace/);
});

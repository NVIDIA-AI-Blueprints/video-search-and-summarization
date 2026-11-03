// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict';
import { register } from 'node:module';
import test from 'node:test';

register('./support/alias-loader.mjs', import.meta.url);

const { containFrame, evidenceFrameState } = await import('../src/lib/evidence/frame-geometry.ts');
const { coversWholeFrame, evidenceDisplay, hitDisplay, videoFrameUrl } = await import('../src/lib/evidence/evidence-view.ts');
const { humanRevisionContent, officialCrops } = await import('../src/modules/reports/domain/revision.ts');
const { structuredFromPayload } = await import('../src/modules/reports/domain/payload-view.ts');

const evidence = (over = {}) => ({
  image: 'evidence/v/run-1/E1.jpg',
  imageUrl: 'https://signed/crop',
  frame: null,
  frameUrl: null,
  located: null,
  boxSeconds: null,
  imageGap: null,
  boundingBox: null,
  timestampSeconds: 4,
  uploaded: false,
  ...over,
});

test('a frame fits its tile whole: wider frames fill the width, taller ones the height', () => {
  assert.deepEqual(containFrame(16 / 9, 16 / 9), { w: 1, h: 1 });
  assert.deepEqual(containFrame(2, 1), { w: 1, h: 0.5 });
  assert.deepEqual(containFrame(0.5, 1), { w: 0.5, h: 1 });
  assert.deepEqual(containFrame(0, 1), { w: 1, h: 1 });
});

test('the whole frame comes first when asked for, then the stored image, then a placeholder', () => {
  assert.equal(evidenceFrameState({ src: 's', frameSrc: 'f', box: [1, 1, 5, 5], fullFrame: true }), 'full-frame');
  assert.equal(evidenceFrameState({ src: 's', frameSrc: null, fullFrame: true }), 'image');
  assert.equal(evidenceFrameState({ fullFrame: true }), 'missing');
});

test('a stored frame is shown with its box', () => {
  const shown = evidenceDisplay(evidence({ frameUrl: 'https://signed/frame', located: true, boundingBox: [100, 100, 300, 400] }), '189');
  assert.equal(shown.frameSrc, 'https://signed/frame');
  assert.deepEqual(shown.box, [100, 100, 300, 400]);
  assert.equal(shown.notLocated, false);
});

test('an object the agent did not locate shows its frame with no box and a note', () => {
  const shown = evidenceDisplay(evidence({ image: null, imageUrl: null, frameUrl: 'https://signed/frame', located: false }), '189');
  assert.equal(shown.frameSrc, 'https://signed/frame');
  assert.equal(shown.box, null);
  assert.equal(shown.notLocated, true);
});

test('an item analysed before frames were stored gets its frame cut from the stored video at the box second', () => {
  const shown = evidenceDisplay(evidence({ boundingBox: [100, 100, 300, 400], timestampSeconds: 4.2 }), '189');
  assert.equal(shown.frameSrc, '/api/reports/189/frame?t=4.2');
  assert.deepEqual(shown.box, [100, 100, 300, 400]);
  assert.equal(shown.src, 'https://signed/crop');
  assert.equal(evidenceDisplay(evidence({ boundingBox: [100, 100, 300, 400], boxSeconds: 7 }), '1').frameSrc, '/api/reports/1/frame?t=7');
});

test('the old whole-frame fallback box is read as not located, not drawn', () => {
  assert.equal(coversWholeFrame([0, 0, 1000, 1000]), true);
  assert.equal(coversWholeFrame([10, 10, 990, 990]), true);
  assert.equal(coversWholeFrame([100, 100, 900, 900]), false);
  const shown = evidenceDisplay(evidence({ boundingBox: [0, 0, 1000, 1000] }), '189');
  assert.equal(shown.notLocated, true);
  assert.equal(shown.box, null);
  assert.equal(shown.frameSrc, '/api/reports/189/frame?t=4');
});

test('an item with no box, frame or time shows its stored image whole', () => {
  const shown = evidenceDisplay(evidence({ timestampSeconds: null }), '189');
  assert.equal(shown.frameSrc, null);
  assert.equal(shown.src, 'https://signed/crop');
  assert.deepEqual(evidenceDisplay(null, '1'), { frameSrc: null, src: null, box: null, notLocated: false });
});

test('the frame route URL escapes the video id and clamps the second', () => {
  assert.equal(videoFrameUrl('a b', -3), '/api/reports/a%20b/frame?t=0');
  assert.equal(videoFrameUrl('5', 7.6), '/api/reports/5/frame?t=7.6', 'the exact second Save cuts, not a whole one');
  assert.equal(videoFrameUrl('5', 7.123456), '/api/reports/5/frame?t=7.123');
  assert.equal(videoFrameUrl('5', 7), '/api/reports/5/frame?t=7');
});

test('the payload view reads the frame, located flag, box second and gap from an evidence entry', () => {
  const payload = { incident: {}, entities: [{ entity_id: 'E1', type: 'human', timestamp_seconds: 3 }], instruments: [], assets: [] };
  const entry = {
    item_type: 'entity',
    item_id: 'E1',
    image: null,
    bounding_box: null,
    timestamp_seconds: 3,
    frame: 'evidence/v/run-1/E1-frame.jpg',
    located: false,
    box_seconds: 3,
    image_gap: 'object not located',
  };
  const { entities } = structuredFromPayload(payload, [entry]);
  assert.deepEqual(
    {
      frame: entities[0].evidence.frame,
      located: entities[0].evidence.located,
      boxSeconds: entities[0].evidence.boxSeconds,
      gap: entities[0].evidence.imageGap,
    },
    { frame: 'evidence/v/run-1/E1-frame.jpg', located: false, boxSeconds: 3, gap: 'object not located' },
  );
});

test('a human edit keeps the frame of an object that was not located while the reviewer leaves it be', () => {
  const notLocated = {
    item_type: 'entity',
    item_id: 'E1',
    image: null,
    bounding_box: null,
    timestamp_seconds: 3,
    frame: 'k-frame.jpg',
    located: false,
  };
  const located = {
    item_type: 'entity',
    item_id: 'E2',
    image: 'k2.jpg',
    bounding_box: [1, 1, 5, 5],
    timestamp_seconds: 4,
    frame: 'k2-frame.jpg',
    located: true,
  };
  const item = (id, image) => ({ entity_id: id, source_id: id, image, type: 'human' });
  const edit = (entities) => ({
    incident: { start_timestamp: 0, end_timestamp: 5 },
    entities,
    instruments: [],
    assets: [],
  });
  const kept = humanRevisionContent({ payload: {}, evidence: [notLocated, located] }, edit([item('E1', null), item('E2', 'k2.jpg')]));
  assert.deepEqual(
    kept.evidence.map((entry) => [entry.item_id, entry.frame]),
    [
      ['E1', 'k-frame.jpg'],
      ['E2', 'k2-frame.jpg'],
    ],
  );
  const removed = humanRevisionContent({ payload: {}, evidence: [notLocated, located] }, edit([item('E1', null), item('E2', null)]));
  assert.deepEqual(
    removed.evidence.map((entry) => entry.item_id),
    ['E1'],
    'removing a located image still drops its entry',
  );
});

test("a search hit shows its video's frame with the box, or a not-located note for the old whole-frame box", () => {
  const hit = { timestampSeconds: 81, boundingBox: [100, 200, 300, 400], cropImageUrl: 'https://crop' };
  const shown = hitDisplay(hit, '189');
  assert.equal(shown.frameSrc, '/api/reports/189/frame?t=81');
  assert.deepEqual(shown.box, [100, 200, 300, 400]);
  assert.equal(shown.notLocated, false);
  assert.equal(shown.src, 'https://crop');
  const whole = hitDisplay({ ...hit, boundingBox: [0, 0, 1000, 1000] }, '189');
  assert.equal(whole.box, null);
  assert.equal(whole.notLocated, true);
  assert.equal(hitDisplay(hit, '').frameSrc, null);
});

test('a searchable crop row takes the second the box was found at, else the item second', () => {
  const vector = new Array(1024).fill(0);
  const embeddings = [
    { itemType: 'asset', itemId: 'A1', embedding: vector },
    { itemType: 'entity', itemId: 'E1', embedding: vector },
  ];
  const evidence = [
    { item_type: 'asset', item_id: 'A1', image: 'a.jpg', bounding_box: [1, 1, 5, 5], timestamp_seconds: 8, box_seconds: 9 },
    { item_type: 'entity', item_id: 'E1', image: 'e.jpg', bounding_box: [1, 1, 5, 5], timestamp_seconds: 23 },
  ];
  const rows = officialCrops({ entities: [], instruments: [], assets: [] }, evidence, embeddings);
  assert.deepEqual(
    rows.map((row) => [row.item_id, row.timestamp_seconds]),
    [
      ['A1', 9],
      ['E1', 23],
    ],
  );
});

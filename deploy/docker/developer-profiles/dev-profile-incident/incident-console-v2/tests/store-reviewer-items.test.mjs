// SPDX-License-Identifier: Apache-2.0

// Save's reviewer items: what the new entry keeps from the entry it replaces, and what it drops because the new image,
// frame and track make it stale.

import assert from 'node:assert/strict';
import { register } from 'node:module';
import { test } from 'node:test';

register('./support/alias-loader.mjs', import.meta.url);

const { storeReviewerItems } = await import('../src/modules/reports/application/store-reviewer-items/index.ts');

const fakeStore = () => {
  const frames = [];
  const puts = [];
  return {
    frames,
    puts,
    frame: async (_videoKey, seconds, region) => {
      frames.push({ seconds, region });
      return Buffer.from('jpeg');
    },
    exists: async () => false,
    put: async (key) => {
      puts.push(key);
    },
  };
};

const retracked = {
  entities: [
    {
      entity_id: 'E1',
      source_id: 'E1',
      reviewer: {
        ai_proposal: null,
        track: {
          source: 'reviewer',
          samples: [
            { t: 7.6, box: [100, 100, 500, 400] },
            { t: 7.85, box: [110, 110, 510, 410] },
          ],
          span: [7.6, 7.85],
          partial: false,
          lostAt: null,
        },
        still_seconds: null,
        still_box: null,
      },
    },
  ],
  instruments: [],
  assets: [],
};

const aiEntry = {
  item_type: 'entity',
  item_id: 'E1',
  timestamp_seconds: 8,
  label: 'man in red',
  image: 'evidence/v.mp4/run-1/E1.jpg',
  frame: 'evidence/v.mp4/run-1/E1-frame.jpg',
  bounding_box: [90, 90, 520, 420],
  detected_box: [100, 100, 500, 400],
  box_seconds: 8,
  located: true,
  image_gap: null,
  track_gap: "no person track overlapped this person's box",
  zoom_checked: true,
  zoom_confidence: 0.9,
  zoom_seen: { frame: 'a man', close_up: 'a man in red' },
  name_before: 'person',
  description_before: 'a person',
  zoom_gap: null,
};

test("a re-tracked item drops the old crop's box, second look and gaps, and cuts at the first sample's exact second", async () => {
  const store = fakeStore();
  const entries = await storeReviewerItems(store, 'v.mp4', retracked, [aiEntry]);
  const entry = entries.get('entity:E1');
  for (const field of [
    'detected_box',
    'zoom_checked',
    'zoom_confidence',
    'zoom_seen',
    'zoom_gap',
    'name_before',
    'description_before',
    'image_gap',
    'track_gap',
  ])
    assert.equal(field in entry, false, `${field} described the old crop`);
  assert.equal(entry.label, 'man in red', 'what is not about the image is kept');
  assert.equal(entry.box_seconds, 7.6);
  assert.deepEqual(entry.bounding_box, [100, 100, 500, 400]);
  assert.equal(entry.track_source, 'reviewer');
  assert.ok(entry.image.includes('edit-') && entry.frame !== aiEntry.frame, 'a new image and frame, nothing overwritten');
  assert.deepEqual(
    store.frames.map((frame) => [frame.seconds, frame.region === null ? 'frame' : 'crop']),
    [
      [7.6, 'crop'],
      [7.6, 'frame'],
    ],
    "crop and frame are cut sequentially at the first sample's exact second",
  );
});

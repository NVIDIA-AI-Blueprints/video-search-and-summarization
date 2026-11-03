// SPDX-License-Identifier: Apache-2.0

// Edit mode's track scrubber: a scrubber value as a whole second inside the track, the box at that second, a track
// that cannot be read leaving the ordinary picker, and the pick saved through the unchanged human revision path.

import assert from 'node:assert/strict';
import { register } from 'node:module';
import test from 'node:test';

register('./support/alias-loader.mjs', import.meta.url);

const { scrubberBoxAt, trackSecond, trackSpan } = await import('../src/lib/evidence/track.ts');
const { parseTrackSamples } = await import('../src/modules/reports/domain/track.ts');
const { boxRegion } = await import('../src/lib/evidence/frame-geometry.ts');
const { parseRegion } = await import('../src/lib/evidence/frame-request.ts');
const { editFromDraft } = await import('../src/modules/reports/domain/edit-draft.ts');
const { humanRevisionContent } = await import('../src/modules/reports/domain/revision.ts');

// A track as the agent stores it: [{t, box, score}], box [top, left, bottom, right] on 0..1000.
const TRACK = [
  { t: 80.4, box: [100, 200, 500, 300], score: 0.9 },
  { t: 81.2, box: [110, 220, 510, 320], score: 0.92 },
  { t: 82.0, box: [300, 240, 700, 400], score: -0.1 },
  { t: 84.6, box: [320, 260, 720, 420], score: 0.88 },
];

test('the scrubber spans the whole seconds inside the track', () => {
  assert.deepEqual(trackSpan(parseTrackSamples(TRACK)), { first: 81, last: 84 });
  assert.deepEqual(trackSpan(parseTrackSamples([{ t: 3, box: [1, 2, 3, 4] }])), { first: 3, last: 3 });
  assert.deepEqual(
    trackSpan(parseTrackSamples([TRACK[0], { t: 80.8, box: [1, 2, 3, 4] }])),
    { first: 81, last: 81 },
    'a track inside one second stops at its nearest whole second',
  );
});

test('a scrubber value becomes a whole second, clamped at the track ends', () => {
  const span = { first: 81, last: 84 };
  assert.equal(trackSecond(82.4, span), 82);
  assert.equal(trackSecond(82.6, span), 83);
  assert.equal(trackSecond(10, span), 81, 'before the track starts: its first second');
  assert.equal(trackSecond(200, span), 84, 'after the track ends: its last second');
  assert.equal(trackSecond(Number.NaN, span), 81);
});

test('the box at a second follows the track, interpolated between close samples and held across a gap', () => {
  const samples = parseTrackSamples(TRACK);
  assert.deepEqual(scrubberBoxAt(samples, 81.2), [110, 220, 510, 320], 'on a sample: its box');
  assert.deepEqual(scrubberBoxAt(samples, 81.6), [205, 230, 605, 360], 'between close samples: interpolated, on the whole grid');
  assert.deepEqual(scrubberBoxAt(samples, 83), [300, 240, 700, 400], 'inside a gap the tracker left: the nearest sample, never none');
  assert.deepEqual(scrubberBoxAt(samples, 84), [320, 260, 720, 420]);
  assert.deepEqual(scrubberBoxAt(samples, 0), [100, 200, 500, 300], 'never extrapolated past the ends');
});

test('the track box at a second is a crop region the crop route accepts', () => {
  const region = boxRegion(scrubberBoxAt(parseTrackSamples(TRACK), 82));
  assert.deepEqual(parseRegion(region), region);
});

test('a second and box picked on the track are saved as a new human revision, as a drawn frame is', () => {
  const parent = {
    payload: { incident: { type: 'assault' }, entities: [{ entity_id: 'E1', type: 'human' }] },
    evidence: [
      {
        item_type: 'entity',
        item_id: 'E1',
        image: 'evidence/v/run-1/E1.jpg',
        bounding_box: [100, 200, 500, 300],
        timestamp_seconds: 82,
        box_seconds: 81.2,
        track: 'evidence/v/run-1/E1-track.json',
      },
    ],
  };
  const box = scrubberBoxAt(parseTrackSamples(TRACK), 84);
  const item = {
    key: 'entity-0',
    sourceId: 'E1',
    timestampSeconds: 82,
    label: 'man',
    image: 'evidence/v/edit-1/entity-0.jpg',
    imageUrl: null,
    pendingImage: null,
    imageTimestampSeconds: 84,
    imageBox: [...box],
    canonicalId: 'E1',
    type: 'human',
    description: 'A man.',
    firstSeenSeconds: 80,
    lastSeenSeconds: 85,
  };
  const draft = {
    incident: { type: 'assault', start_timestamp: 80, end_timestamp: 86, description: 'A fall.', severity_level: 2 },
    entities: [item],
    instruments: [],
    assets: [],
  };
  const { evidence } = humanRevisionContent(parent, editFromDraft(draft));
  assert.deepEqual(evidence, [
    {
      item_type: 'entity',
      item_id: 'E1',
      image: 'evidence/v/edit-1/entity-0.jpg',
      bounding_box: [320, 260, 720, 420],
      timestamp_seconds: 84,
      source: 'upload',
    },
  ]);
});

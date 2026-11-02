// SPDX-License-Identifier: Apache-2.0

// Person tracks in the report: reading the track file, the overlay's box at a second under contain-fit, the presence bar,
// the view mapping of an evidence entry's track, and the track use case's key handling.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { register } from 'node:module';
import { join } from 'node:path';
import { test } from 'node:test';

register('./support/alias-loader.mjs', import.meta.url);

const {
  containedBoxStyle,
  parseTrackSamples,
  presenceRegion,
  presenceSeekSeconds,
  trackBoxAt,
  trackColour,
  trackedReportItems,
  TRACK_TOLERANCE_SECONDS,
} = await import('../src/modules/reports/domain/track.ts');
const { structuredFromPayload } = await import('../src/modules/reports/domain/payload-view.ts');
const { entityDetail, evidenceDetailLines } = await import('../src/modules/reports/domain/evidence-detail.ts');
const { getEvidenceTrack } = await import('../src/modules/reports/application/get-evidence-track/index.ts');
const { evidenceTrackUrl, getReport } = await import('../src/modules/reports/application/get-report/index.ts');
const { InMemoryIncidentRepository } = await import('../src/modules/reports/infrastructure/memory/incidents.ts');
const { aiRunReport } = await import('./support/report-view.mjs');

const smoke = JSON.parse(readFileSync(join(process.cwd(), 'tests', 'fixtures', 'contract-v2-smoke-run.json'), 'utf8'));
const TRACK_KEY = 'evidence/uploads/v.mp4/run-1/E1-track.json';
const samples = [
  { t: 10, box: [100, 100, 300, 200] },
  { t: 10.2, box: [100, 120, 300, 220] },
  { t: 10.4, box: [100, 140, 300, 240] },
];

test('a track file is read leniently: malformed samples dropped, sorted by time, anything but a list is null', () => {
  assert.equal(parseTrackSamples({ t: 1 }), null);
  assert.equal(parseTrackSamples(null), null);
  assert.deepEqual(
    parseTrackSamples([
      { t: 2, box: [0, 0, 10, 10], score: 0.9 },
      { t: 1, box: [0, 0, 10, 10], score: -0.1 },
      { t: 'x', box: [0, 0, 10, 10] },
      { t: 3, box: [0, 0, 10] },
      { t: 4, box: [10, 10, 5, 5] },
      { t: -1, box: [0, 0, 10, 10] },
      null,
    ]),
    [
      { t: 1, box: [0, 0, 10, 10] },
      { t: 2, box: [0, 0, 10, 10] },
    ],
  );
});

test('the box at a second interpolates between close samples and holds the nearest at the span ends', () => {
  assert.deepEqual(trackBoxAt(samples, 10.1), [100, 110, 300, 210]);
  assert.deepEqual(trackBoxAt(samples, 10.2), [100, 120, 300, 220]);
  assert.deepEqual(trackBoxAt(samples, 10.4 + TRACK_TOLERANCE_SECONDS), [100, 140, 300, 240]);
  assert.deepEqual(trackBoxAt(samples, 10 - TRACK_TOLERANCE_SECONDS), [100, 100, 300, 200]);
});

test('the box is hidden outside the track span plus the tolerance, and inside a gap the tracker lost the person', () => {
  assert.equal(trackBoxAt(samples, 9.4), null);
  assert.equal(trackBoxAt(samples, 11), null);
  assert.equal(trackBoxAt([], 10), null);
  const gapped = [
    { t: 1, box: [0, 0, 100, 100] },
    { t: 5, box: [0, 400, 100, 500] },
  ];
  assert.equal(trackBoxAt(gapped, 3), null, 'never drawn sliding across a four-second gap');
  assert.deepEqual(trackBoxAt(gapped, 1.3), [0, 0, 100, 100]);
});

test('under contain-fit a wide frame is letterboxed and a tall frame pillarboxed, and the box follows the picture', () => {
  const box = [0, 0, 500, 500];
  // A 16:9 frame in a square tile fills the width and 9/16 of the height, centred.
  assert.deepEqual(containedBoxStyle(box, 16 / 9, 1), { left: '0%', top: '21.875%', width: '50%', height: '28.125%' });
  // A 9:16 frame in a 16:9 tile fills the height and (9/16)/(16/9) of the width, centred.
  const fit = 9 / 16 / (16 / 9);
  const style = containedBoxStyle([500, 500, 1000, 1000], 9 / 16, 16 / 9);
  assert.equal(style.top, '50%');
  assert.equal(style.height, '50%');
  assert.equal(style.left, `${+(((1 - fit) / 2 + 0.5 * fit) * 100).toFixed(3)}%`);
  assert.equal(style.width, `${+(0.5 * fit * 100).toFixed(3)}%`);
  // A tile of the frame's own shape is the plain box.
  assert.deepEqual(containedBoxStyle([100, 200, 300, 400], 16 / 9, 16 / 9), { left: '20%', top: '10%', width: '20%', height: '20%' });
  assert.equal(containedBoxStyle(null, 1, 1), null);
});

test('the presence bar places the span over the clip, keeps a short span visible and seeks into the span', () => {
  const near = (actual, expected) => {
    assert.ok(Math.abs(actual.left - expected.left) < 1e-9 && Math.abs(actual.width - expected.width) < 1e-9, JSON.stringify(actual));
  };
  near(presenceRegion(20, 60, 100), { left: 0.2, width: 0.4 });
  near(presenceRegion(50, 50, 100), { left: 0.5, width: 0.01 });
  near(presenceRegion(99.9, 100, 100), { left: 0.99, width: 0.01 });
  near(presenceRegion(80, 140, 100), { left: 0.8, width: 0.2 });
  assert.equal(presenceRegion(20, 60, null), null);
  assert.equal(presenceRegion(60, 20, 100), null);
  assert.equal(presenceRegion(120, 130, 100), null);
  assert.equal(presenceSeekSeconds(0.25, 80, 15, 30), 20, 'a click on the span seeks to the second under it');
  assert.equal(presenceSeekSeconds(0.9, 80, 15, 30), 15, 'a click on empty clip seeks to the span start');
  assert.equal(presenceSeekSeconds(-1, 80, 15, 30), 15, 'a key press seeks to the span start');
});

const payload = {
  incident: {},
  entities: [
    { entity_id: 'E1', type: 'human', label: 'man in red', timestamp_seconds: 82, first_seen_seconds: 80, last_seen_seconds: 90 },
    { entity_id: 'E2', type: 'human', label: 'man in blue', timestamp_seconds: 82 },
    { entity_id: 'E3', type: 'human', label: 'woman', timestamp_seconds: 30 },
  ],
  instruments: [],
  assets: [],
};
const entry = (id, extra = {}) => ({ item_type: 'entity', item_id: id, image: null, bounding_box: null, timestamp_seconds: 82, ...extra });

test('the view carries a track when the entry has its key and both span ends, and nothing otherwise', () => {
  const { entities } = structuredFromPayload(payload, [
    entry('E1', { track: TRACK_KEY, track_first_seen_seconds: 78.2, track_last_seen_seconds: 95.6 }),
    entry('E2'),
    entry('E3', { track: TRACK_KEY.replace('E1', 'E3'), track_first_seen_seconds: 'x', track_last_seen_seconds: 40 }),
  ]);
  const track = ({ evidence }) => ({
    track: evidence.track,
    trackUrl: evidence.trackUrl,
    first: evidence.trackFirstSeenSeconds,
    last: evidence.trackLastSeenSeconds,
  });
  assert.deepEqual(track(entities[0]), { track: TRACK_KEY, trackUrl: null, first: 78.2, last: 95.6 });
  const none = { track: null, trackUrl: null, first: null, last: null };
  assert.deepEqual(track(entities[1]), none, 'an entry without a track behaves as before');
  assert.deepEqual(track(entities[2]), none, 'a malformed span reads as no track');

  const lines = entities.map((entity) => evidenceDetailLines(entityDetail(entity, [])));
  assert.equal(lines[0].onScreen, 'On screen 1:20–1:30', "the report's own span is never rewritten");
  assert.equal(lines[0].trackedOnScreen, 'On screen 1:18–1:35 (tracked)');
  assert.equal(lines[1].trackedOnScreen, null);
});

test('only tracked items are drawn, each with its own colour in id order', () => {
  const structured = structuredFromPayload(payload, [
    entry('E1', { track: TRACK_KEY, track_first_seen_seconds: 78, track_last_seen_seconds: 95 }),
    entry('E2'),
    entry('E3', { track: TRACK_KEY.replace('E1', 'E3'), track_first_seen_seconds: 20, track_last_seen_seconds: 40 }),
  ]);
  for (const entity of structured.entities) if (entity.evidence?.track) entity.evidence.trackUrl = `/t/${entity.entityId}`;
  const items = trackedReportItems(structured);
  assert.deepEqual(
    items.map(({ id, colour, firstSeconds, lastSeconds, label }) => [id, colour, firstSeconds, lastSeconds, label]),
    [
      ['E1', trackColour(0), 78, 95, 'man in red'],
      ['E3', trackColour(1), 20, 40, 'woman'],
    ],
  );
  assert.notEqual(trackColour(0), trackColour(1));
});

test('a track label is the item name, falling back to its id only when it has no name', () => {
  const structured = structuredFromPayload(payload, [
    entry('E1', { track: TRACK_KEY, track_first_seen_seconds: 78, track_last_seen_seconds: 95 }),
  ]);
  structured.entities[0].evidence.trackUrl = '/t/E1';
  structured.entities[0].label = '';
  structured.entities[0].type = '';
  assert.equal(trackedReportItems(structured)[0].label, 'E1');
  structured.entities[0].label = 'man in red';
  assert.equal(trackedReportItems(structured)[0].label, 'man in red');
});

function trackedRecord() {
  const record = aiRunReport(smoke);
  record.evidence = [
    entry('E1', { track: TRACK_KEY, track_first_seen_seconds: 1, track_last_seen_seconds: 9 }),
    entry('E2', { track: 'uploads/v.mp4' }),
  ];
  const view = structuredFromPayload(record.payload, record.evidence);
  record.entities = view.entities;
  return record;
}

test('getReport points a tracked item at the console track route and leaves the others without one', async () => {
  const incidents = new InMemoryIncidentRepository([trackedRecord()]);
  const view = await getReport(incidents, smoke.videoId, null, async (key) => `https://signed/${key}`);
  const [e1, e2] = view.structured.entities;
  assert.equal(e1.evidence.trackUrl, evidenceTrackUrl(smoke.videoId, view.revisionId, 'entity', 'E1'));
  assert.match(e1.evidence.trackUrl, /^\/api\/reports\/[^?]+\/track\?revision=\d+&type=entity&item=E1$/);
  assert.equal(e2.evidence?.trackUrl ?? null, null);
});

test('the track use case reads only the key the revision names, and a malformed file is an empty track', async () => {
  const incidents = new InMemoryIncidentRepository([trackedRecord()]);
  const { revisionId } = (await incidents.getRevision(smoke.videoId, null)).revision;
  const read = [];
  const store = (body) => async (key) => {
    read.push(key);
    return body;
  };
  const request = { videoId: smoke.videoId, revisionId, itemType: 'entity', itemId: 'E1' };
  assert.deepEqual(await getEvidenceTrack(incidents, store([{ t: 1, box: [0, 0, 10, 10], score: 0.9 }]), request), [
    { t: 1, box: [0, 0, 10, 10] },
  ]);
  assert.deepEqual(read, [TRACK_KEY]);
  assert.deepEqual(await getEvidenceTrack(incidents, store({ not: 'a list' }), request), []);
  assert.equal(await getEvidenceTrack(incidents, store([]), { ...request, itemId: 'E2' }), null, 'not a track key');
  assert.equal(await getEvidenceTrack(incidents, store([]), { ...request, itemId: 'E9' }), null);
  assert.equal(await getEvidenceTrack(incidents, store([]), { ...request, revisionId: revisionId + 50 }), null);
  assert.deepEqual(read, [TRACK_KEY, TRACK_KEY]);
});

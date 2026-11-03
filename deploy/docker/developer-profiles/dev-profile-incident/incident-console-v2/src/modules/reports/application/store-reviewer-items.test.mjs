// SPDX-License-Identifier: Apache-2.0

// Reviewer items (box tool): Save cuts the crop and frame at the first-seen second, stores the track under edit-<N>/ and
// writes the evidence entry; View reads it back with its badges. Fakes only: no R2, no ffmpeg, no agent.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { register } from 'node:module';
import { join } from 'node:path';
import { test } from 'node:test';

register('../../../../tests/support/alias-loader.mjs', import.meta.url);

const { InMemoryIncidentRepository } = await import('../../../../src/modules/reports/infrastructure/memory/incidents.ts');
const { InMemoryVideoRepository } = await import('../../../../src/modules/videos/infrastructure/memory/videos.ts');
const { saveHumanEdit } = await import('@/modules/reports/application/save-human-edit');
const { StoreRejection } = await import('@/modules/shared/domain/common');
const { validateStructuredEdit } = await import('@/modules/contract/domain/structured-edit');
const { addReviewerItem, draftFromView, editFromDraft } = await import('@/modules/reports/domain/edit-draft');
const { structuredFromPayload } = await import('@/modules/reports/domain/payload-view');
const { evidenceDetailLines, assetDetail } = await import('@/modules/reports/domain/evidence-detail');
const { trackedReportItems } = await import('@/modules/reports/domain/track');
const { toReportView } = await import('@/modules/reports/application/report-view');
const { aiRunReport } = await import('../../../../tests/support/report-view.mjs');

const smoke = JSON.parse(readFileSync(join(process.cwd(), 'tests', 'fixtures', 'contract-v2-smoke-run.json'), 'utf8'));
const V = smoke.videoId;
const K = smoke.video.filepath;

const proposal = {
  itemType: 'asset',
  name: 'red backpack',
  description: 'A red backpack on the bench.',
  holder: null,
  threatLevel: null,
  confidence: 0.8,
};
const track = (partial = false) => ({
  source: 'reviewer',
  samples: [
    { t: 3.4, box: [100, 100, 300, 200], conf: 0.9 },
    { t: 3.8, box: [100, 110, 300, 210], conf: 0.8 },
    { t: 5, box: [100, 150, 300, 250], conf: 0.7 },
  ],
  span: [3.4, 5],
  partial,
  lostAt: partial ? 5.5 : null,
});
const reviewerItem = (overrides = {}) => ({
  itemType: 'asset',
  name: 'red backpack',
  description: 'A red backpack.',
  aiProposal: proposal,
  track: track(),
  ...overrides,
});

function setup() {
  const record = aiRunReport(smoke);
  const incidents = new InMemoryIncidentRepository([record]);
  const videos = new InMemoryVideoRepository([
    { videoId: V, storageKey: K, uploadedAt: null, source: null, officialRevisionId: 1, revisionIds: [1] },
  ]);
  return { record, ports: { incidents, videos } };
}

/** A store that records what was cut and written; `taken` are keys that already exist. */
function fakeStore(taken = []) {
  const store = {
    frames: [],
    puts: new Map(),
    async frame(_key, seconds, region) {
      store.frames.push({ seconds, region });
      return Buffer.from(region ? 'crop' : 'frame');
    },
    async exists(key) {
      return taken.includes(key) || store.puts.has(key);
    },
    async put(key, body, type) {
      store.puts.set(key, { body: body.toString(), type });
    },
  };
  return store;
}

function editWith(record, item) {
  const draft = addReviewerItem(draftFromView(toReportView(record, 'https://video')), item);
  // The box tool's items have no pending image; an incident field the form requires is already present in the fixture.
  return validateStructuredEdit(JSON.parse(JSON.stringify(editFromDraft(draft))));
}

const noEmbed = async (evidence) => evidence.map((e) => ({ itemType: e.item_type, itemId: e.item_id, embedding: [0.5] }));

test('a reviewer item is saved with its crop and frame cut at the first-seen second and its track under edit-<N>/', async () => {
  const { record, ports } = setup();
  const store = fakeStore();
  const edit = editWith(record, reviewerItem());
  const newId = edit.assets.at(-1).asset_id;

  const saved = await saveHumanEdit(ports, { videoId: V, parentId: 1, edit, createdBy: 'Rev' }, noEmbed, store);

  assert.deepEqual(
    store.frames.map((f) => [f.seconds, f.region === null ? 'frame' : 'crop']).sort(),
    [
      [3.4, 'crop'],
      [3.4, 'frame'],
    ],
    'both are cut at the track first sample, not at a picked second',
  );
  assert.deepEqual(store.frames.find((f) => f.region).region, { x: 0.1, y: 0.1, w: 0.1, h: 0.2 });
  const base = `evidence/${K}/edit-1/${newId}`;
  assert.deepEqual([...store.puts.keys()].sort(), [`${base}-frame.jpg`, `${base}-track.json`, `${base}.jpg`]);
  const file = JSON.parse(store.puts.get(`${base}-track.json`).body);
  assert.deepEqual([file.source, file.partial, file.lost_at, file.samples.length], ['reviewer', false, null, 3]);

  const stored = await ports.incidents.getRevision(V, saved.revisionId);
  const entry = stored.evidence.find((e) => e.item_id === newId && e.item_type === 'asset');
  assert.equal(entry.image, `${base}.jpg`);
  assert.equal(entry.frame, `${base}-frame.jpg`);
  assert.deepEqual([entry.bounding_box, entry.box_seconds, entry.source], [[100, 100, 300, 200], 3.4, 'reviewer']);
  assert.deepEqual(
    [entry.track, entry.track_first_seen_seconds, entry.track_last_seen_seconds, entry.track_source, entry.track_partial],
    [`${base}-track.json`, 3.4, 5, 'reviewer', false],
  );
  assert.equal(entry.ai_proposal.name, 'red backpack', 'the proposal is kept for audit');
  assert.ok(!JSON.stringify(stored.payload).includes('reviewer'), 'reviewer data never leaks into the report payload');
});

test('a still image has a crop and frame but no track; the next edit number is free', async () => {
  const { record, ports } = setup();
  const edit = editWith(record, reviewerItem({ track: null, stillSeconds: 7, stillBox: [10, 20, 30, 40] }));
  const newId = edit.assets.at(-1).asset_id;
  const store = fakeStore([`evidence/${K}/edit-1/${newId}.jpg`]);
  const saved = await saveHumanEdit(ports, { videoId: V, parentId: 1, edit, createdBy: 'Rev' }, noEmbed, store);
  const entry = (await ports.incidents.getRevision(V, saved.revisionId)).evidence.find((e) => e.item_id === newId);
  assert.ok(entry.image.includes('/edit-2/'), 'edit-1 was taken, so nothing is overwritten');
  assert.deepEqual([entry.box_seconds, entry.bounding_box, 'track' in entry], [7, [10, 20, 30, 40], false]);
});

test('an incomplete track is stored as partial', async () => {
  const { record, ports } = setup();
  const edit = editWith(record, reviewerItem({ track: track(true) }));
  const saved = await saveHumanEdit(ports, { videoId: V, parentId: 1, edit, createdBy: 'Rev' }, noEmbed, fakeStore());
  const stored = await ports.incidents.getRevision(V, saved.revisionId);
  const entry = stored.evidence.find((e) => e.track_partial !== undefined);
  assert.equal(entry.track_partial, true);
});

test('a re-tracked item keeps its source entry apart from the image, frame and track; an AI item is not "added by reviewer"', async () => {
  const record = aiRunReport(smoke);
  record.evidence = [
    {
      item_type: 'asset',
      item_id: 'A1',
      image: 'evidence/old.jpg',
      bounding_box: [1, 2, 3, 4],
      timestamp_seconds: 2,
      item_name: 'white van',
      track: 'evidence/old-track.json',
      track_first_seen_seconds: 1,
      track_last_seen_seconds: 2,
    },
  ];
  const incidents = new InMemoryIncidentRepository([record]);
  const videos = new InMemoryVideoRepository([
    { videoId: V, storageKey: K, uploadedAt: null, source: null, officialRevisionId: 1, revisionIds: [1] },
  ]);
  const view = toReportView(record, 'https://video');
  const draft = draftFromView(view);
  draft.assets[0].reviewer = reviewerItem({ name: 'white van' });
  const edit = validateStructuredEdit(JSON.parse(JSON.stringify(editFromDraft(draft))));
  const saved = await saveHumanEdit({ incidents, videos }, { videoId: V, parentId: 1, edit, createdBy: 'Rev' }, noEmbed, fakeStore());
  const entry = (await incidents.getRevision(V, saved.revisionId)).evidence.find((e) => e.item_id === 'A1');
  assert.equal(entry.item_name, 'white van');
  assert.equal(entry.track_source, 'reviewer');
  assert.ok(entry.track.includes('/edit-1/A1-track.json'));
  assert.equal('source' in entry, false);
  const lines = evidenceDetailLines(
    assetDetail(structuredFromPayload({ assets: [{ asset_id: 'A1', name: 'white van', description: '' }] }, [entry]).assets[0]),
  );
  assert.deepEqual(lines.badges, []);
});

test('a reviewer item cannot be saved without frame storage, and a bad box is refused', async () => {
  const { record, ports } = setup();
  const edit = editWith(record, reviewerItem());
  await assert.rejects(
    saveHumanEdit(ports, { videoId: V, parentId: 1, edit, createdBy: 'Rev' }, noEmbed),
    (error) => error instanceof StoreRejection && error.reason === 'invalid',
  );
  const bad = JSON.parse(JSON.stringify(edit));
  bad.assets.at(-1).reviewer.track.samples[0].box = [0, 0, 2000, 10];
  assert.throws(() => validateStructuredEdit(bad), /does not satisfy/);
});

test('View: a reviewer item shows "Added by reviewer" and "Incomplete track"; the player labels it by name', () => {
  const entry = {
    item_type: 'asset',
    item_id: 'A1',
    image: 'evidence/x.jpg',
    bounding_box: [1, 2, 3, 4],
    timestamp_seconds: 3,
    source: 'reviewer',
    track: 'evidence/x-track.json',
    track_first_seen_seconds: 3.4,
    track_last_seen_seconds: 5,
    track_source: 'reviewer',
    track_partial: true,
  };
  const structured = structuredFromPayload({ assets: [{ asset_id: 'A1', name: 'red backpack', description: 'd', timestamp_seconds: 3 }] }, [
    entry,
  ]);
  const evidence = structured.assets[0].evidence;
  assert.deepEqual([evidence.addedByReviewer, evidence.trackSource, evidence.trackPartial], [true, 'reviewer', true]);
  assert.deepEqual(evidenceDetailLines(assetDetail(structured.assets[0])).badges, ['Added by reviewer', 'Incomplete track']);
  structured.assets[0].evidence.trackUrl = '/api/track';
  const [tracked] = trackedReportItems(structured);
  assert.equal(tracked.label, 'red backpack', 'the track is labelled with the name, never A1');

  const whole = structuredFromPayload({ assets: [{ asset_id: 'A1', name: 'n', description: '' }] }, [{ ...entry, track_partial: false }]);
  assert.deepEqual(evidenceDetailLines(assetDetail(whole.assets[0])).badges, ['Added by reviewer']);
});

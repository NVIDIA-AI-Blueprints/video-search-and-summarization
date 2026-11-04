// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { register } from 'node:module';
import { test } from 'node:test';

register('./support/alias-loader.mjs', import.meta.url);

const { buildTimelineLanes, timelineDuration, axisPercent, axisTicks } = await import('../src/lib/reports/timeline-lanes.ts');
const { layoutVersions } = await import('../src/lib/reports/version-graph.ts');
const { markOfficial, clearOfficial } = await import('../src/lib/reports/official-client.ts');
const { structuredFromPayload } = await import('../src/modules/reports/domain/payload-view.ts');

const entity = (id, first, last, extra = {}) => ({
  entityId: id,
  type: 'person',
  label: 'Guard',
  description: '',
  timestampSeconds: 5,
  firstSeenSeconds: first,
  lastSeenSeconds: last,
  evidence: null,
  ...extra,
});
const instrument = (id, entityId) => ({
  instrumentId: id,
  entityId,
  name: id,
  description: '',
  threatLevel: 2,
  timestampSeconds: 7,
  evidence: null,
});
const asset = (id) => ({ assetId: id, name: id, description: '', timestampSeconds: 9, evidence: null });

test('timeline lanes: one per entity with held instruments, then unknown holder and assets', () => {
  const lanes = buildTimelineLanes({
    entities: [entity('E1', 2, 20), entity('E2', null, null)],
    instruments: [instrument('I1', 'E1'), instrument('I2', null), instrument('I3', 'E9')],
    assets: [asset('A1')],
  });
  assert.deepEqual(
    lanes.map((lane) => lane.id),
    ['E1', 'E2', 'unknown-holder', 'assets'],
  );
  assert.deepEqual(lanes[0].span, { start: 2, end: 20 });
  assert.deepEqual(
    lanes[0].markers.map((marker) => marker.kind),
    ['entity', 'instrument'],
  );
  assert.equal(lanes[1].span, null, 'an edited revision with no span falls back to its single marker');
  assert.equal(lanes[2].markers.length, 2);
});

test('timeline lanes: no unknown-holder or assets lane when empty; invalid span ignored', () => {
  const lanes = buildTimelineLanes({ entities: [entity('E1', 10, 3)], instruments: [], assets: [] });
  assert.equal(lanes.length, 1);
  assert.equal(lanes[0].span, null);
});

test('timeline axis helpers', () => {
  assert.equal(timelineDuration([], 30), 30);
  assert.equal(timelineDuration(buildTimelineLanes({ entities: [entity('E1', 2, 20)], instruments: [], assets: [] }), null), 20);
  assert.equal(axisPercent(50, 40), 100);
  assert.equal(axisPercent(-1, 40), 0);
  assert.deepEqual(axisTicks(30), [0, 5, 10, 15, 20, 25, 30]);
});

const rev = (revisionId, revisionNo, parentId, kind = 'ai') => ({
  revisionId,
  videoId: 'v',
  revisionNo,
  kind,
  parentId,
  isOfficial: false,
});

test('version graph: each AI run starts a lane, human versions follow their parent', () => {
  const { rows, laneCount } = layoutVersions([rev(4, 4, null), rev(3, 3, 2, 'human'), rev(2, 2, 1, 'human'), rev(1, 1, null)]);
  assert.equal(laneCount, 2);
  assert.deepEqual(
    rows.map((row) => row.lane),
    [1, 0, 0, 0],
  );
  const lane0 = rows[1].segments.find((segment) => segment.lane === 0);
  assert.deepEqual(lane0, { lane: 0, fromAbove: false, toBelow: true });
});

test('official client surfaces the route error, such as the agent being unreachable', async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push([url, init.method]);
    return { ok: false, json: async () => ({ error: 'The agent is unreachable' }) };
  };
  try {
    await assert.rejects(markOfficial('v 1', 3), /The agent is unreachable/);
    await assert.rejects(clearOfficial('v 1'), /unreachable/);
    assert.deepEqual(calls, [
      ['/api/videos/v%201/official', 'PUT'],
      ['/api/videos/v%201/official', 'DELETE'],
    ]);
    globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });
    await markOfficial('v', 1);
  } finally {
    globalThis.fetch = original;
  }
});

test('the entity seen span is mapped into the report view', () => {
  const view = structuredFromPayload(
    {
      incident: {},
      entities: [{ entity_id: 'E1', type: 'person', description: 'd', timestamp_seconds: 3, first_seen_seconds: 1, last_seen_seconds: 9 }],
      instruments: [],
      assets: [],
    },
    [],
  );
  assert.equal(view.entities[0].firstSeenSeconds, 1);
  assert.equal(view.entities[0].lastSeenSeconds, 9);
});

test('the report page has a mark-official star, no Restore, and keeps the layout while loading', async () => {
  const components = new URL('../src/sections/', import.meta.url);
  const versions = await readFile(new URL('reports/components/ReportVersions/ReportVersions.tsx', components), 'utf8');
  const page = await readFile(new URL('reports/components/ReportPage/ReportPage.tsx', components), 'utf8');
  const viewPane = await readFile(new URL('reports/components/ReportPane/ReportPane.tsx', components), 'utf8');
  const editPane = await readFile(new URL('reports/components/ReportEditPane/ReportEditPane.tsx', components), 'utf8');
  assert.match(versions, /Mark v\$\{revision\.revisionNo\} official/);
  assert.doesNotMatch(versions + page, /restore/i);
  assert.match(page, /Try again/);
  assert.match(viewPane + editPane, /No official version has been selected/);
  assert.match(editPane, /officialMissing &&/);
  assert.doesNotMatch(page, /mode === 'edit' && !officialRevision && revisions/);
});

test('a search hit links to its report at the hit second, and the page reads that second back', async () => {
  const { reportHref, parseStartSeconds } = await import('../src/lib/library/search.ts');
  const hit = { resultId: 'q1', item: { timestampSeconds: 42.7 }, incident: { videoId: 'Assault 007' } };
  assert.equal(reportHref(hit), '/reports/Assault%20007?t=42');
  assert.equal(reportHref(hit, { revisionId: 9, revisionNo: 2, isOfficial: true }), '/reports/Assault%20007?revision=9&t=42');
  assert.equal(parseStartSeconds('42'), 42);
  assert.equal(parseStartSeconds('0'), 0);
  assert.equal(parseStartSeconds('3.5'), 3.5);
  for (const bad of [null, undefined, '', ' ', '-1', 'soon', 'NaN', 'Infinity'])
    assert.equal(parseStartSeconds(bad), null, `rejects ${bad}`);
});

test('the report page seeks to ?t once, when the player has loaded its metadata', async () => {
  const page = await readFile(new URL('../src/sections/reports/components/ReportPage/ReportPage.tsx', import.meta.url), 'utf8');
  assert.match(page, /startSeconds = null/);
  assert.match(
    page,
    /if \(startSeconds !== null && !startedAt\.current\) \{\s*startedAt\.current = true;\s*event\.currentTarget\.currentTime = startSeconds;\s*\}/,
  );
  assert.match(page, /id="back-to-library"[^>]*>\s*← Library\s*</);
});

test('the report workspace fits the viewport and keeps its action bar outside the details scroller', async () => {
  const route = await readFile(new URL('../src/app/reports/[videoId]/page.tsx', import.meta.url), 'utf8');
  const page = await readFile(new URL('../src/sections/reports/components/ReportPage/ReportPage.tsx', import.meta.url), 'utf8');
  assert.match(route, /flex h-dvh flex-col overflow-hidden/);
  assert.match(page, /h-full min-h-0 overflow-hidden/);
  assert.match(page, /min-h-0 flex-1 overflow-y-auto" data-testid="report-details-pane"/);
  assert.match(page, /data-testid="report-details-pane"[\s\S]*?Edit from v\{report\.revision\.revisionNo\}/);
});

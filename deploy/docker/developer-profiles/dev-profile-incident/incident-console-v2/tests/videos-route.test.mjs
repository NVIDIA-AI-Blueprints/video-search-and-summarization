// SPDX-License-Identifier: Apache-2.0

// Characterisation test for GET /api/videos, written against the pre-refactor
// route: the row -> list item mapping and pagination moved into the video adapter.

import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { register } from 'node:module';
import { afterEach, beforeEach, test } from 'node:test';

register('./support/alias-loader.mjs', import.meta.url);

const { GET } = await import('../src/app/api/videos/route.ts');
const originalFetch = globalThis.fetch;
const originalEnv = { ...process.env };
beforeEach(() => {
  Object.assign(process.env, { INCIDENT_SUPABASE_URL: 'https://supabase.test', INCIDENT_SUPABASE_SERVICE_ROLE_KEY: 'k' });
});
afterEach(() => {
  globalThis.fetch = originalFetch;
  process.env = { ...originalEnv };
});

const ROWS = [
  {
    id: 'v1',
    filepath: 'uploads/sensor-1/clip.mp4',
    uploaded_datetime: '2026-10-01T08:00:00',
    source: 'sensor-1',
    official_revision_id: 3,
    report_revisions: [{ id: 1 }, { id: 2 }, { id: 3 }],
  },
  { id: 'v2', filepath: null, uploaded_datetime: null, source: null, official_revision_id: null, report_revisions: [] },
  { id: 'v3', filepath: 'anomaly/burglary/', uploaded_datetime: '2026-09-01T00:00:00', source: 7, official_revision_id: null },
];

test('videos are listed with their revision count, official revision and pagination', async () => {
  let request;
  globalThis.fetch = async (url) => {
    request = new URL(String(url));
    return new Response(JSON.stringify(ROWS), { headers: { 'content-range': '20-22/43' } });
  };
  const response = await GET(new Request('http://localhost/api/videos?page=2'));
  assert.equal(response.status, 200);
  assert.equal(request.pathname, '/rest/v1/videos');
  assert.equal(request.searchParams.get('limit'), '20');
  assert.equal(request.searchParams.get('offset'), '20');
  assert.equal(request.searchParams.get('order'), 'uploaded_datetime.desc.nullslast,id.asc');
  assert.equal(
    request.searchParams.get('select'),
    'id,filepath,uploaded_datetime,source,official_revision_id,report_revisions!report_revisions_video_id_fkey(id)',
  );
  assert.deepEqual(await response.json(), {
    videos: [
      { videoId: 'v1', filename: 'clip.mp4', uploadedAt: '2026-10-01T08:00:00', source: 'sensor-1', revisions: 3, officialRevisionId: 3 },
      { videoId: 'v2', filename: 'v2', uploadedAt: null, source: null, revisions: 0, officialRevisionId: null },
      { videoId: 'v3', filename: 'v3', uploadedAt: '2026-09-01T00:00:00', source: null, revisions: 0, officialRevisionId: null },
    ],
    pagination: { page: 2, pageSize: 20, totalItems: 43, totalPages: 3 },
    filter: 'all',
  });
});

test('filters with-report and needs-analysis forward proper queries to Supabase', async () => {
  let selectQuery = '';
  globalThis.fetch = async (url) => {
    selectQuery = new URL(String(url)).searchParams.get('select') || '';
    return new Response(JSON.stringify([]), { headers: { 'content-range': '0-0/0' } });
  };
  const resWith = await GET(new Request('http://localhost/api/videos?filter=with-report'));
  assert.equal(resWith.status, 200);
  assert.match(selectQuery, /!inner\(id\)/);

  const resNeeds = await GET(new Request('http://localhost/api/videos?filter=needs-analysis'));
  assert.equal(resNeeds.status, 200);
  assert.match(selectQuery, /\(id\)/);

  const resFailed = await GET(new Request('http://localhost/api/videos?filter=failed'));
  assert.equal(resFailed.status, 200);
  const jsonFailed = await resFailed.json();
  assert.deepEqual(jsonFailed.videos, []);
});

test('an unknown filter is rejected before Supabase', async () => {
  let called = false;
  globalThis.fetch = async () => {
    called = true;
    return new Response('[]');
  };
  assert.equal((await GET(new Request('http://localhost/api/videos?filter=everything'))).status, 400);
  assert.equal(called, false);
});

test('the retired video-history page is absent', async () => {
  await assert.rejects(readFile(new URL('../src/app/videos/[videoId]/page.tsx', import.meta.url), 'utf8'), { code: 'ENOENT' });
});

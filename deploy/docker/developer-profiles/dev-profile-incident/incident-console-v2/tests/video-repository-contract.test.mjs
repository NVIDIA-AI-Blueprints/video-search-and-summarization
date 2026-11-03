// SPDX-License-Identifier: Apache-2.0

// One VideoRepository contract, run against the in-memory fake and the Supabase
// adapter over a fake PostgREST whose set_official_revision RPC mimics
// supabase/functions-final.sql (tests/support/revisions-database.mjs).
// listVideos' embedded select is covered by tests/videos-route.test.mjs.

import assert from 'node:assert/strict';
import { register } from 'node:module';
import { afterEach, describe, test } from 'node:test';

import { fakePostgrest } from './support/fake-postgrest.mjs';
import { revisionRpcs } from './support/revisions-database.mjs';

register('./support/alias-loader.mjs', import.meta.url);

const { InMemoryVideoRepository } = await import('../src/modules/videos/infrastructure/memory/videos.ts');
const { SupabaseVideoAdapter } = await import('../src/modules/videos/infrastructure/supabase/videos.ts');
const { StoreRejection } = await import('../src/modules/shared/domain/common.ts');
const { PostgrestClient } = await import('../src/lib/postgrest/client.ts');

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
});

const VIDEO = {
  videoId: 'v1',
  storageKey: 'uploads/sensor-1/v1.mp4',
  uploadedAt: '2026-10-01T08:00:00',
  source: 'sensor-1',
  officialRevisionId: null,
};
const attempt = (id, videoId, status) => ({
  id,
  notes: JSON.stringify({ incidentConsoleV2: { recordType: 'analysis_attempt', status, videoId } }),
});
const CROPS = [
  {
    item_type: 'entity',
    item_id: 'E1',
    image: 'evidence/v1/u/E1.jpg',
    bounding_box: [1, 2, 3, 4],
    timestamp_seconds: 2,
    item_name: 'human',
    description: 'A person.',
    threat_level: null,
    holder_entity_id: null,
    embedding: [0.1, 0.2],
  },
];
const REVISIONS = [
  {
    id: 1,
    video_id: 'v1',
    revision_no: 1,
    kind: 'ai',
    model_run_id: 'm1',
    evidence: [
      { item_type: 'entity', item_id: 'E1', image: 'evidence/v1/u/E1.jpg' },
      { item_type: 'asset', item_id: 'A1', image: null },
    ],
  },
  { id: 2, video_id: 'v2', revision_no: 1, kind: 'ai', model_run_id: 'm-other', evidence: [] },
];

const implementations = {
  memory: () => ({ repo: new InMemoryVideoRepository([{ ...VIDEO, revisionIds: [1] }]) }),
  supabase: () => {
    const db = fakePostgrest(
      {
        videos: [
          { id: 'v1', filepath: VIDEO.storageKey, uploaded_datetime: VIDEO.uploadedAt, source: VIDEO.source, official_revision_id: null },
        ],
        report_revisions: REVISIONS,
        evidence_crops: [],
        model_runs: [
          attempt('m-failed', 'v1', 'contract_failed'),
          attempt('m-other', 'v2', 'contract_failed'),
          attempt('m1', 'v1', 'valid_first_pass'),
        ],
      },
      { rpc: revisionRpcs },
    );
    globalThis.fetch = db.fetch;
    return { repo: new SupabaseVideoAdapter(new PostgrestClient('https://supabase.test', 'k')), store: db.store, db };
  },
};

const rejection = (reason, message) => (error) => error instanceof StoreRejection && error.reason === reason && message.test(error.message);

for (const [name, make] of Object.entries(implementations)) {
  describe(`VideoRepository contract: ${name}`, () => {
    test('a video is read by id; an unknown one is null', async () => {
      const { repo } = make();
      assert.deepEqual(await repo.getVideo('v1'), VIDEO);
      assert.equal(await repo.getVideo('v-none'), null);
    });

    test('a revision of the video becomes official and can be cleared', async () => {
      const { repo } = make();
      assert.deepEqual(await repo.setOfficialRevision('v1', 1, CROPS), { videoId: 'v1', officialRevisionId: 1, crops: 1 });
      assert.equal((await repo.getVideo('v1')).officialRevisionId, 1);
      assert.deepEqual(await repo.setOfficialRevision('v1', null, []), { videoId: 'v1', officialRevisionId: null, crops: 0 });
      assert.equal((await repo.getVideo('v1')).officialRevisionId, null);
    });

    test('refusals are domain rejections, not transport errors', async () => {
      const { repo } = make();
      await assert.rejects(repo.setOfficialRevision('v-none', 1, []), rejection('not-found', /Video not found/));
      await assert.rejects(repo.setOfficialRevision('v1', 2, []), rejection('not-found', /Revision not found/), "another video's revision");
      await assert.rejects(repo.setOfficialRevision('v1', 99, []), rejection('not-found', /Revision not found/));
      assert.equal((await repo.getVideo('v1')).officialRevisionId, null);
    });

    test('the searchable revision takes crops without changing the official pointer', async () => {
      const { repo } = make();
      // No official revision: the newest revision of v1 (id 1) is the searchable one.
      assert.deepEqual(await repo.setRevisionCrops('v1', 1, CROPS), { videoId: 'v1', revisionId: 1, crops: 1 });
      assert.equal((await repo.getVideo('v1')).officialRevisionId, null);
      assert.deepEqual(await repo.setRevisionCrops('v1', 1, []), { videoId: 'v1', revisionId: 1, crops: 0 });
    });

    test('crops are refused for an unknown video or a revision that is not the searchable one', async () => {
      const { repo } = make();
      await assert.rejects(repo.setRevisionCrops('v-none', 1, []), rejection('not-found', /Video not found/));
      await assert.rejects(
        repo.setRevisionCrops('v1', 2, []),
        rejection('invalid', /not the searchable revision/),
        "another video's revision",
      );
      await assert.rejects(repo.setRevisionCrops('v1', 99, []), rejection('invalid', /not the searchable revision/));
    });

    test('a deleted video is gone', async () => {
      const { repo } = make();
      await repo.deleteVideo('v1');
      assert.equal(await repo.getVideo('v1'), null);
    });
  });
}

describe('SupabaseVideoAdapter', () => {
  test('marking official is one set_official_revision call with the crops in the RPC shape', async () => {
    const { repo, db, store } = implementations.supabase();
    await repo.setOfficialRevision('v1', 1, CROPS);
    assert.deepEqual(db.requests, [
      {
        method: 'POST',
        path: 'rpc/set_official_revision',
        body: { p_video_id: 'v1', p_revision_id: 1, p_crops: CROPS },
      },
    ]);
    assert.deepEqual(
      store.evidence_crops.map((crop) => [crop.revision_id, crop.item_id, crop.embedding]),
      [[1, 'E1', [0.1, 0.2]]],
    );
    await repo.setOfficialRevision('v1', null, []);
    assert.deepEqual(store.evidence_crops, []);
  });

  test('storing crops is one set_revision_crops call with the crops in the RPC shape', async () => {
    const { repo, db, store } = implementations.supabase();
    await repo.setRevisionCrops('v1', 1, CROPS);
    assert.deepEqual(db.requests, [
      {
        method: 'POST',
        path: 'rpc/set_revision_crops',
        body: { p_video_id: 'v1', p_revision_id: 1, p_crops: CROPS },
      },
    ]);
    assert.deepEqual(
      store.evidence_crops.map((crop) => [crop.revision_id, crop.item_id, crop.embedding]),
      [[1, 'E1', [0.1, 0.2]]],
    );
  });

  test('deleting a video removes only the run records that name it and that no revision uses', async () => {
    const { repo, store } = implementations.supabase();
    await repo.deleteVideo('v1');
    // m1 is still used by revision 1 in this fake, which does not cascade; m-failed is not.
    assert.deepEqual(store.model_runs.map((row) => row.id).sort(), ['m-other', 'm1']);
    store.report_revisions = [];
    await repo.deleteVideo('v1');
    assert.deepEqual(store.model_runs.map((row) => row.id).sort(), ['m-other']);
  });

  test('other PostgREST failures are not turned into rejections', async () => {
    globalThis.fetch = async () => new Response('{"message":"permission denied"}', { status: 401 });
    const repo = new SupabaseVideoAdapter(new PostgrestClient('https://supabase.test', 'k'));
    await assert.rejects(
      repo.setOfficialRevision('v1', 1, []),
      (error) => !(error instanceof StoreRejection) && /HTTP 401/.test(error.message),
    );
  });
});

// SPDX-License-Identifier: Apache-2.0

// The background analysis queue: the pure job rules (modules/jobs/domain/job.ts),
// the in-memory repository built on them, one loop tick (runNextJob), the
// enqueue/retry/cancel use cases, the Supabase adapter's PostgREST calls and
// the /api/jobs routes. The SQL functions themselves are tested on a real
// Postgres in supabase/tests/test_baseline.py.

import assert from 'node:assert/strict';
import { register } from 'node:module';
import { afterEach, beforeEach, test } from 'node:test';

import { fakePostgrest } from './support/fake-postgrest.mjs';

register('./support/alias-loader.mjs', import.meta.url);

const domain = await import('../src/modules/jobs/domain/job.ts');
const { InMemoryAnalysisJobRepository } = await import('../src/modules/jobs/infrastructure/memory/analysis-jobs.ts');
const { SupabaseAnalysisJobAdapter, jobFromRow } = await import('../src/modules/jobs/infrastructure/supabase/analysis-jobs.ts');
const { PostgrestClient } = await import('../src/lib/postgrest/client.ts');
const { StoreRejection } = await import('../src/modules/shared/domain/common.ts');
const { AnalysisRequestError } = await import('../src/modules/analysis/application/run-contract-analysis.ts');
const app = await import('../src/modules/jobs/application/index.ts');
const jobsRoute = await import('../src/app/api/jobs/route.ts');
const retryRoute = await import('../src/app/api/jobs/[jobId]/retry/route.ts');
const cancelRoute = await import('../src/app/api/jobs/[jobId]/cancel/route.ts');

const NOW = new Date('2026-10-31T08:00:00Z');
const at = (seconds) => new Date(NOW.getTime() + seconds * 1000);
const stamp = (seconds) => at(seconds).toISOString().replace('Z', '');

function job(id, extra = {}) {
  return {
    id,
    filePath: `uploads/clip-${id}.mp4`,
    filename: `clip-${id}.mp4`,
    model: 'model-a',
    status: 'queued',
    attempts: 0,
    error: null,
    createdAt: stamp(id),
    startedAt: null,
    finishedAt: null,
    heartbeatAt: null,
    videoId: null,
    revisionId: null,
    ...extra,
  };
}

const enqueue = (repo, name) => repo.enqueue({ filePath: `uploads/${name}`, filename: name, model: 'model-a' });

/** Runs scheduled callbacks only when the test fires them. */
function manualTimers() {
  const timers = new Set();
  return {
    every: (_ms, fn) => {
      timers.add(fn);
      return () => timers.delete(fn);
    },
    fire: () => {
      for (const fn of timers) fn();
    },
    get count() {
      return timers.size;
    },
  };
}

const catalog = { findAllowedModel: (id) => (['model-a', 'model-b'].includes(id) ? { id, label: id, timeout_seconds: 300 } : undefined) };

test('job rules: the oldest queued job is claimed next, ties by id', () => {
  const jobs = [job(3), job(1, { status: 'running' }), job(2), job(4, { createdAt: stamp(2) })];
  assert.equal(domain.nextToClaim(jobs).id, 2);
  assert.equal(domain.nextToClaim([job(1, { status: 'done' })]), null);
  const claimed = domain.claimed(job(2, { error: 'old' }), NOW);
  assert.deepEqual(
    [claimed.status, claimed.attempts, claimed.error, claimed.startedAt, claimed.heartbeatAt],
    ['running', 1, null, stamp(0), stamp(0)],
  );
  assert.equal(domain.holdsClaim(claimed, 1), true);
  assert.equal(domain.holdsClaim(claimed, 2), false);
});

test('job rules: a stale running job is queued again after its first stale claim and failed after the second', () => {
  const running = job(1, { status: 'running', attempts: 1, startedAt: stamp(0), heartbeatAt: stamp(0) });
  assert.equal(domain.recoverStale(running, at(domain.STALE_SECONDS - 1)), running, 'a recent heartbeat is left alone');
  const requeued = domain.recoverStale(running, at(domain.STALE_SECONDS + 1));
  assert.deepEqual([requeued.status, requeued.attempts, requeued.heartbeatAt], ['queued', 1, null]);
  const second = domain.recoverStale({ ...running, attempts: 2 }, at(600));
  assert.deepEqual([second.status, second.error, second.finishedAt], ['failed', domain.STALE_ERROR, stamp(600)]);
  assert.equal(domain.recoverStale(job(2), at(9999)).status, 'queued', 'only running jobs go stale');
  assert.equal(domain.isStale({ ...running, heartbeatAt: null }, at(domain.STALE_SECONDS + 1)), true, 'falls back to the start');
});

test('job rules: retry takes a failed or cancelled job; cancel only a queued one; finish clears the heartbeat', () => {
  for (const status of domain.JOB_STATUSES) {
    assert.equal(domain.canRetry({ status }), status === 'failed' || status === 'cancelled', status);
    assert.equal(domain.canCancel({ status }), status === 'queued', status);
  }
  const retried = domain.retried(job(1, { status: 'failed', attempts: 2, error: 'x', finishedAt: stamp(5) }));
  assert.deepEqual([retried.status, retried.attempts, retried.error, retried.finishedAt], ['queued', 0, null, null]);
  const running = domain.claimed(job(1), NOW);
  const done = domain.finished(running, { status: 'done', videoId: 'v1', revisionId: 9 }, at(60));
  assert.deepEqual([done.status, done.videoId, done.revisionId, done.heartbeatAt, done.finishedAt], ['done', 'v1', 9, null, stamp(60)]);
  assert.equal(domain.finished(running, { status: 'failed', error: '  ' }, NOW).error, domain.DEFAULT_FAILURE);
});

test('job rules: the list shows running, then queued in run order, then the latest finished', () => {
  const jobs = [
    job(1, { status: 'done' }),
    job(2),
    job(3, { status: 'failed' }),
    job(4, { status: 'running' }),
    job(5),
    job(6, { status: 'cancelled' }),
  ];
  assert.deepEqual(
    domain.jobsList(jobs, 2).map((entry) => entry.id),
    [4, 2, 5, 6, 3],
  );
});

test('memory repository: claims one at a time, oldest first, and recovers a stopped console through the stale rule', async () => {
  const repo = new InMemoryAnalysisJobRepository(NOW);
  const a = await enqueue(repo, 'a.mp4');
  repo.advance(1);
  const b = await enqueue(repo, 'b.mp4');
  const first = await repo.claim(domain.STALE_SECONDS);
  assert.equal(first.id, a.id);
  assert.equal((await repo.claim(domain.STALE_SECONDS)).id, b.id);
  assert.equal(await repo.claim(domain.STALE_SECONDS), null);
  // The console stops: no heartbeat for longer than the stale limit.
  repo.advance(domain.STALE_SECONDS + 1);
  const again = await repo.claim(domain.STALE_SECONDS);
  assert.deepEqual([again.id, again.attempts], [a.id, 2], 'the first stale run goes back to the queue and is claimed again');
  assert.equal(await repo.heartbeat(a.id, 1), false, 'the stopped runner lost its claim');
  assert.equal(await repo.finish(a.id, 1, { status: 'done', videoId: 'v', revisionId: 1 }), null);
  repo.advance(domain.STALE_SECONDS + 1);
  await repo.claim(domain.STALE_SECONDS);
  assert.equal(repo.jobs.get(a.id).status, 'failed', 'a second stale run fails');
  assert.equal(repo.jobs.get(a.id).error, domain.STALE_ERROR);
  assert.equal((await repo.retry(a.id)).status, 'queued');
  assert.equal(await repo.retry(a.id), null, 'a queued job is not retried');
  assert.equal((await repo.cancel(a.id)).status, 'cancelled');
  assert.equal(await repo.cancel(a.id), null);
});

test('runNextJob: heartbeats while it analyses, then stores done with the video and revision', async () => {
  const repo = new InMemoryAnalysisJobRepository(NOW);
  await enqueue(repo, 'a.mp4');
  const timers = manualTimers();
  let heartbeats = 0;
  const original = repo.heartbeat.bind(repo);
  repo.heartbeat = async (...args) => {
    heartbeats += 1;
    return original(...args);
  };
  const seen = [];
  const finished = await app.runNextJob({
    jobs: repo,
    every: timers.every,
    warn: (message) => seen.push(message),
    analyse: async (claimedJob) => {
      assert.equal(claimedJob.status, 'running');
      assert.equal(timers.count, 1, 'the heartbeat runs while the analysis does');
      repo.advance(15);
      timers.fire();
      await new Promise((resolve) => setImmediate(resolve));
      return { videoId: 'v-a', revisionId: 11 };
    },
  });
  assert.equal(heartbeats, 1);
  assert.equal(timers.count, 0, 'the heartbeat stops with the analysis');
  assert.deepEqual([finished.status, finished.videoId, finished.revisionId], ['done', 'v-a', 11]);
  assert.deepEqual(seen, []);
  assert.equal(await app.runNextJob({ jobs: repo, every: timers.every, warn: () => {}, analyse: assert.fail }), null, 'nothing queued');
});

test('runNextJob: a failed analysis stores its plain reason, and the next job still runs', async () => {
  const repo = new InMemoryAnalysisJobRepository(NOW);
  await enqueue(repo, 'broken.mp4');
  await enqueue(repo, 'good.mp4');
  const deps = {
    jobs: repo,
    every: manualTimers().every,
    warn: () => {},
    analyse: async (claimedJob) => {
      if (claimedJob.filename === 'broken.mp4') throw new AnalysisRequestError(502, 'The agent request failed (AGENT_TIMEOUT)');
      return { videoId: 'v-good', revisionId: 2 };
    },
  };
  const failed = await app.runNextJob(deps);
  assert.deepEqual([failed.filename, failed.status, failed.error], ['broken.mp4', 'failed', 'The agent request failed (AGENT_TIMEOUT)']);
  assert.equal((await app.runNextJob(deps)).status, 'done');
});

test('runNextJob: a runner whose claim was lost stops heartbeating and stores nothing', async () => {
  const repo = new InMemoryAnalysisJobRepository(NOW);
  const queued = await enqueue(repo, 'a.mp4');
  const timers = manualTimers();
  const warnings = [];
  const result = await app.runNextJob({
    jobs: repo,
    every: timers.every,
    warn: (message) => warnings.push(message),
    analyse: async () => {
      // Another console decides this one stopped and claims the job itself.
      repo.advance(domain.STALE_SECONDS + 1);
      await repo.claim(domain.STALE_SECONDS);
      timers.fire();
      await new Promise((resolve) => setImmediate(resolve));
      return { videoId: 'v', revisionId: 1 };
    },
  });
  assert.equal(result, null);
  assert.equal(repo.jobs.get(queued.id).status, 'running', 'the newer claim is untouched');
  assert.equal(repo.jobs.get(queued.id).attempts, 2);
  assert.equal(warnings.length, 2, warnings.join('\n'));
});

test('enqueue: checks the file and model and stores the chosen model, or the default', async () => {
  const repo = new InMemoryAnalysisJobRepository(NOW);
  const chosen = await app.enqueueAnalysisJob(
    repo,
    catalog,
    { filePath: 'uploads/a.mp4', filename: 'a.mp4', model: ' model-b ' },
    'model-a',
  );
  assert.deepEqual([chosen.status, chosen.model], ['queued', 'model-b']);
  assert.equal((await app.enqueueAnalysisJob(repo, catalog, { filePath: 'uploads/b.mp4', filename: 'b.mp4' }, 'model-a')).model, 'model-a');
  await assert.rejects(app.enqueueAnalysisJob(repo, catalog, { filePath: '', filename: 'c.mp4' }, 'model-a'), StoreRejection);
  await assert.rejects(
    app.enqueueAnalysisJob(repo, catalog, { filePath: 'uploads/c.mp4', filename: 'c.mp4', model: 'model-x' }, 'model-a'),
    /Unsupported model: model-x/,
  );
  await assert.rejects(app.retryAnalysisJob(repo, chosen.id), /Only a failed or cancelled job/);
  assert.equal((await app.cancelAnalysisJob(repo, chosen.id)).status, 'cancelled');
  await assert.rejects(app.cancelAnalysisJob(repo, chosen.id), /Only a queued job/);
  assert.equal((await app.retryAnalysisJob(repo, chosen.id)).status, 'queued');
});

/** A fake PostgREST whose job functions apply the same rules as the SQL, on the shared domain code. */
function jobsDatabase(rows = []) {
  const toRow = (entry) => ({
    id: entry.id,
    file_path: entry.filePath,
    filename: entry.filename,
    model: entry.model,
    status: entry.status,
    attempts: entry.attempts,
    error: entry.error,
    created_at: entry.createdAt,
    started_at: entry.startedAt,
    finished_at: entry.finishedAt,
    heartbeat_at: entry.heartbeatAt,
    video_id: entry.videoId,
    revision_id: entry.revisionId,
  });
  const calls = [];
  const update = (store, id, change) => {
    const index = store.analysis_jobs.findIndex((row) => row.id === id);
    store.analysis_jobs[index] = toRow(change);
    return [store.analysis_jobs[index]];
  };
  const find = (store, id) => {
    const row = store.analysis_jobs.find((entry) => entry.id === id);
    return row ? jobFromRow(row) : null;
  };
  const db = fakePostgrest(
    { analysis_jobs: rows.map(toRow) },
    {
      identity: { analysis_jobs: 'id' },
      rpc: {
        claim_analysis_job: (args, store) => {
          calls.push(['claim', args]);
          const next = domain.nextToClaim(store.analysis_jobs.map(jobFromRow));
          return next ? update(store, next.id, domain.claimed(next, NOW)) : [];
        },
        touch_analysis_job: (args, store) => {
          calls.push(['touch', args]);
          const found = find(store, args.p_id);
          return Boolean(found && domain.holdsClaim(found, args.p_attempt));
        },
        finish_analysis_job: (args, store) => {
          calls.push(['finish', args]);
          const found = find(store, args.p_id);
          if (!found || !domain.holdsClaim(found, args.p_attempt)) return [];
          const outcome =
            args.p_status === 'done'
              ? { status: 'done', videoId: args.p_video_id, revisionId: args.p_revision_id }
              : { status: 'failed', error: args.p_error ?? '' };
          return update(store, found.id, domain.finished(found, outcome, NOW));
        },
        retry_analysis_job: (args, store) => {
          calls.push(['retry', args]);
          const found = find(store, args.p_id);
          return found && domain.canRetry(found) ? update(store, found.id, domain.retried(found)) : [];
        },
        cancel_analysis_job: (args, store) => {
          calls.push(['cancel', args]);
          const found = find(store, args.p_id);
          return found && domain.canCancel(found) ? update(store, found.id, domain.cancelled(found, NOW)) : [];
        },
      },
    },
  );
  // The table's column defaults, which the fake does not apply itself.
  const defaults = { status: 'queued', attempts: 0, error: null, created_at: stamp(0), started_at: null, finished_at: null };
  const fetch = (url, init = {}) =>
    init.method === 'POST' && String(url).endsWith('/rest/v1/analysis_jobs')
      ? db.fetch(url, { ...init, body: JSON.stringify({ ...defaults, ...JSON.parse(String(init.body)) }) })
      : db.fetch(url, init);
  return { ...db, fetch, calls };
}

const originalFetch = globalThis.fetch;
const originalEnv = { ...process.env };
afterEach(() => {
  globalThis.fetch = originalFetch;
  for (const key of Object.keys(process.env)) if (!(key in originalEnv)) delete process.env[key];
  Object.assign(process.env, originalEnv);
});

test('Supabase adapter: enqueues a row and drives the job functions with their p_* arguments', async () => {
  const db = jobsDatabase();
  globalThis.fetch = db.fetch;
  const repo = new SupabaseAnalysisJobAdapter(new PostgrestClient('https://supabase.test', 'key'));
  const queued = await repo.enqueue({ filePath: 'uploads/a.mp4', filename: 'a.mp4', model: 'model-a' });
  assert.deepEqual(
    Object.keys(db.requests[0].body).filter((key) => ['file_path', 'filename', 'model'].includes(key)),
    ['file_path', 'filename', 'model'],
  );
  assert.equal(queued.id, 1);
  const claimed = await repo.claim(180);
  assert.deepEqual(db.calls.at(-1), ['claim', { p_stale_seconds: 180 }]);
  assert.deepEqual([claimed.status, claimed.attempts], ['running', 1]);
  assert.equal(await repo.claim(180), null, 'zero rows is no job');
  assert.equal(await repo.heartbeat(1, 1), true);
  assert.equal(await repo.heartbeat(1, 2), false);
  const done = await repo.finish(1, 1, { status: 'done', videoId: 'v1', revisionId: 4 });
  assert.deepEqual(db.calls.at(-1), [
    'finish',
    { p_id: 1, p_attempt: 1, p_status: 'done', p_error: null, p_video_id: 'v1', p_revision_id: 4 },
  ]);
  assert.deepEqual([done.status, done.videoId, done.revisionId], ['done', 'v1', 4]);
  assert.equal(await repo.cancel(1), null);
  assert.equal(await repo.retry(1), null, 'a done job is not retried');
});

test('Supabase adapter: lists active jobs and the latest finished ones in one order', async () => {
  const db = jobsDatabase([
    job(1, { status: 'done' }),
    job(2, { status: 'failed', error: 'bad' }),
    job(3, { status: 'running', attempts: 1 }),
    job(4),
  ]);
  globalThis.fetch = db.fetch;
  const repo = new SupabaseAnalysisJobAdapter(new PostgrestClient('https://supabase.test', 'key'));
  const listed = await repo.list(10);
  assert.deepEqual(
    listed.map((entry) => [entry.id, entry.status]),
    [
      [3, 'running'],
      [4, 'queued'],
      [2, 'failed'],
      [1, 'done'],
    ],
  );
  assert.equal(listed[2].error, 'bad');
  assert.throws(() => jobFromRow({ id: 9, status: 'paused' }), /unknown status/);
});

function routeEnv(db) {
  process.env.INCIDENT_SUPABASE_URL = 'https://supabase.test';
  process.env.INCIDENT_SUPABASE_SERVICE_ROLE_KEY = 'test-key';
  process.env.VLM_MODEL = 'nvidia/cosmos-3-nano-reasoner';
  globalThis.fetch = db.fetch;
}

const post = (url, body) =>
  new Request(`http://localhost${url}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const params = (jobId) => ({ params: Promise.resolve({ jobId: String(jobId) }) });

test('jobs routes: POST queues an upload with the default model, GET lists it, retry and cancel answer 422 when they cannot', async () => {
  const db = jobsDatabase();
  routeEnv(db);
  const created = await jobsRoute.POST(post('/api/jobs', { filepath: 'uploads/a.mp4', filename: 'a.mp4' }));
  assert.equal(created.status, 201);
  const { job: queued } = await created.json();
  assert.equal(queued.model, 'nvidia/cosmos-3-nano-reasoner');

  const listed = await (await jobsRoute.GET()).json();
  assert.deepEqual(
    listed.jobs.map((entry) => [entry.id, entry.status]),
    [[1, 'queued']],
  );

  assert.equal((await jobsRoute.POST(post('/api/jobs', { filepath: 'uploads/a.mp4' }))).status, 422);
  assert.equal((await jobsRoute.POST(post('/api/jobs', { filepath: 'uploads/a.mp4', filename: 'a.mp4', model: 'nope' }))).status, 400);
  assert.equal((await retryRoute.POST(post('/api/jobs/1/retry', {}), params(1))).status, 422);
  assert.equal((await cancelRoute.POST(post('/api/jobs/1/cancel', {}), params(1))).status, 200);
  assert.equal((await cancelRoute.POST(post('/api/jobs/1/cancel', {}), params(1))).status, 422);
  const retried = await retryRoute.POST(post('/api/jobs/1/retry', {}), params(1));
  assert.equal(retried.status, 200);
  assert.equal((await retried.json()).job.status, 'queued');
  assert.equal((await retryRoute.POST(post('/api/jobs/x/retry', {}), params('x'))).status, 404);
});

beforeEach(() => {
  globalThis.fetch = originalFetch;
});

test('every attempt of a job analyses into the same video id', () => {
  const first = app.jobVideoId(job(7));
  assert.match(first, /^v[0-9a-f]{19}$/);
  assert.equal(app.jobVideoId({ ...job(7), status: 'running', attempts: 2 }), first, 'a re-run or retry keeps the video');
  assert.notEqual(app.jobVideoId(job(8)), first);
});

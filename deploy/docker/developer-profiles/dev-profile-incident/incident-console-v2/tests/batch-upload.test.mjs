// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict';
import test from 'node:test';

import {
  applyJobs,
  canRetryUpload,
  cancelQueued,
  drainQueue,
  itemProgress,
  jobItem,
  NOT_A_VIDEO_MESSAGE,
  newBatchItem,
  nextToRun,
  otherJobs,
  removeQueued,
  requeue,
  runBatchItem,
  shouldAbortRunning,
  summariseBatch,
  UPLOAD_CANCELLED_MESSAGE,
  UPLOAD_REFUSED_MESSAGE,
  waitingJobIds,
} from '../src/sections/analysis/batch-upload.ts';

const file = (name, type = 'video/mp4') => ({ name, size: 2 * 1024 * 1024, type });
const queued = (key) => newBatchItem(file(`${key}.mp4`), key);
const keys = (items) => items.map((item) => item.key);

/** A job as GET /api/jobs returns it. */
function job(id, filename, status = 'queued', extra = {}) {
  return {
    id,
    filePath: `uploads/${filename}`,
    filename,
    model: 'model-a',
    status,
    attempts: status === 'queued' ? 0 : 1,
    error: null,
    createdAt: '2026-10-31T08:00:00',
    startedAt: null,
    finishedAt: null,
    heartbeatAt: null,
    videoId: null,
    revisionId: null,
    ...extra,
  };
}

function harness(overrides = {}) {
  const calls = [];
  const deps = {
    upload: async (f, onProgress, onUploaded) => {
      calls.push(`upload ${f.name}`);
      onProgress(50);
      onUploaded();
      return { filePath: `uploads/${f.name}`, filename: f.name, converted: true };
    },
    enqueue: async ({ filePath, filename, model }) => {
      calls.push(`enqueue ${filename} ${model}`);
      return job(calls.filter((call) => call.startsWith('enqueue')).length, filename, 'queued', { filePath });
    },
    ...overrides,
  };
  return { calls, deps };
}

/** Runs one file and returns its final item plus every status it passed through. */
async function run(f, deps) {
  let item = newBatchItem(f, 'k');
  const statuses = [];
  await runBatchItem(f, 'model-a', deps, (change) => {
    item = { ...item, ...change };
    if (statuses.at(-1) !== item.status) statuses.push(item.status);
  });
  return { item, statuses };
}

/** A queue driven the way the hook drives it: the items live in one array the runs patch. */
function queue(names, deps) {
  let items = names.map((name) => newBatchItem(file(name), name));
  const order = [];
  return {
    get items() {
      return items;
    },
    set items(next) {
      items = next;
    },
    order,
    drain: () =>
      drainQueue(
        () => items,
        async (key) => {
          order.push(key);
          await runBatchItem(file(key), 'model-a', deps, (change) => {
            items = items.map((item) => (item.key === key ? { ...item, ...change } : item));
          });
        },
      ),
  };
}

test('a file passes Uploading and Converting, then waits as a queued job', async () => {
  const { calls, deps } = harness();
  const { item, statuses } = await run(file('a.mov'), deps);
  assert.deepEqual(statuses, ['uploading', 'converting', 'waiting']);
  assert.deepEqual(calls, ['upload a.mov', 'enqueue a.mov model-a']);
  assert.equal(item.jobId, 1);
  assert.equal(item.report, null);
  assert.equal(item.converted, true);
  assert.equal(itemProgress(item), 67);
});

test('a queued row follows its job: Analysing, then Done with the report link', async () => {
  const { deps } = harness();
  const { item } = await run(file('a.mp4'), deps);
  const running = applyJobs([item], [job(1, 'a.mp4', 'running', { startedAt: '2026-10-31T08:01:00' })])[0];
  assert.equal(running.status, 'analysing');
  assert.equal(running.analysingSince, '2026-10-31T08:01:00');
  const done = applyJobs([running], [job(1, 'a.mp4', 'done', { videoId: 'v1', revisionId: 7 })])[0];
  assert.equal(done.status, 'done');
  assert.deepEqual(done.report, { videoId: 'v1', revisionId: 7 });
  assert.equal(itemProgress(done), 100);
  const failed = applyJobs([running], [job(1, 'a.mp4', 'failed', { error: 'The agent request failed (AGENT_TIMEOUT)' })])[0];
  assert.deepEqual([failed.status, failed.failedAt, failed.error], ['failed', 'analysing', 'The agent request failed (AGENT_TIMEOUT)']);
  assert.equal(canRetryUpload(failed), false, 'a failed job is retried on the server, not re-sent');
  assert.deepEqual(applyJobs([running], []), [running], 'a job missing from the list leaves its row as it was');
  const local = queued('b');
  assert.equal(applyJobs([local], [job(1, 'a.mp4', 'done')])[0], local, 'a row without a job is untouched');
});

test('the row takes the stored name once the upload answers', async () => {
  const { deps } = harness({
    upload: async () => ({ filePath: 'uploads/clip-2.mp4', filename: 'clip-2.mp4', converted: false }),
  });
  const { item } = await run(file('clip.MOV'), deps);
  assert.equal(item.name, 'clip-2.mp4');
  assert.equal(item.converted, false);
});

test('a failure is recorded at its step and does not throw', async () => {
  const { deps } = harness({ enqueue: async () => Promise.reject(new Error('Unsupported model: model-x')) });
  const { item } = await run(file('a.mp4'), deps);
  assert.equal(item.status, 'failed');
  assert.equal(item.failedAt, 'analysing');
  assert.equal(item.error, 'Unsupported model: model-x');
  assert.equal(item.jobId, null);

  const refused = harness({
    upload: async (_f, _p, onUploaded) => {
      onUploaded();
      throw new Error('ffprobe could not read the file: Invalid data found when processing input');
    },
  });
  const converting = (await run(file('b.mp4'), refused.deps)).item;
  assert.equal(converting.failedAt, 'converting', 'a file the server refuses after the bytes are up fails at Converting');
  assert.match(converting.error, /Invalid data/);
  assert.equal(canRetryUpload(converting), true, 'Retry re-sends a file that failed before it became a job');
});

test('Retry on a file whose upload failed puts it back in the queue as new', () => {
  const failed = {
    ...queued('a'),
    status: 'failed',
    failedAt: 'converting',
    error: 'ffprobe could not read the file',
    uploadProgress: 100,
  };
  const [again] = requeue([failed], 'a');
  assert.deepEqual([again.status, again.failedAt, again.error, again.uploadProgress], ['queued', null, null, 0]);
  assert.equal(nextToRun([again]), 'a');
  const jobFailed = { ...failed, failedAt: 'analysing', jobId: 3 };
  assert.deepEqual(requeue([jobFailed], 'a'), [jobFailed], 'a failed job is not re-sent');
  assert.deepEqual(requeue([queued('b')], 'b'), [queued('b')], 'only a failed file is requeued');
});

test('a refused upload with no message says the file was not accepted as a video', async () => {
  for (const message of ['', '  ']) {
    const { deps } = harness({ upload: async () => Promise.reject(new Error(message)) });
    const { item } = await run(file('notvideo.mp4'), deps);
    assert.equal(item.status, 'failed');
    assert.equal(item.failedAt, 'uploading');
    assert.equal(item.error, UPLOAD_REFUSED_MESSAGE);
    assert.equal(item.error, 'The file was not accepted as a video');
  }
  const later = harness({ enqueue: async () => Promise.reject(new Error('')) });
  assert.equal(
    (await run(file('a.mp4'), later.deps)).item.error,
    'The video could not be queued for analysis',
    'only the upload steps have the video fallback',
  );
});

test('a file that is not a video by name or type fails at once and is never sent', async () => {
  const item = newBatchItem(file('notes.txt', 'text/plain'), 'n');
  assert.equal(item.status, 'failed');
  assert.equal(item.error, NOT_A_VIDEO_MESSAGE);
  assert.equal(nextToRun([item]), null);
  assert.equal(newBatchItem(file('clip.MOV', ''), 'm').status, 'queued', 'a known extension is enough');
});

test('files upload strictly one at a time, in the order they were added', async () => {
  let running = 0;
  let peak = 0;
  const { deps } = harness();
  const upload = deps.upload;
  deps.upload = async (...args) => {
    running += 1;
    peak = Math.max(peak, running);
    await new Promise((resolve) => setTimeout(resolve, 5));
    running -= 1;
    return upload(...args);
  };
  const q = queue(['a', 'b', 'c'], deps);
  await q.drain();
  assert.deepEqual(q.order, ['a', 'b', 'c']);
  assert.equal(peak, 1);
  assert.deepEqual(
    q.items.map((item) => [item.status, item.jobId]),
    [
      ['waiting', 1],
      ['waiting', 2],
      ['waiting', 3],
    ],
  );
});

test('nothing starts while a file uploads, converts or queues; a file waiting on the server does not hold the next back', () => {
  const items = [{ ...queued('a'), status: 'done' }, { ...queued('b'), status: 'converting' }, queued('c'), queued('d')];
  assert.equal(nextToRun(items), null);
  assert.equal(nextToRun(items.map((item) => (item.key === 'b' ? { ...item, status: 'failed' } : item))), 'c');
  assert.equal(nextToRun([{ ...queued('a'), status: 'done' }]), null);
  assert.equal(nextToRun([{ ...queued('a'), status: 'waiting' }, queued('b')]), null, 'its job is still being queued');
  assert.equal(nextToRun([{ ...queued('a'), status: 'waiting', jobId: 1 }, queued('b')]), 'b');
  assert.equal(nextToRun([{ ...queued('a'), status: 'analysing', jobId: 1 }, queued('b')]), 'b');
});

test('one failing file never stops the ones after it', async () => {
  const { deps } = harness({
    upload: async (f, _p, onUploaded) => {
      if (f.name === 'b') throw new Error('ffprobe could not read the file');
      onUploaded();
      return { filePath: `uploads/${f.name}`, filename: f.name, converted: false };
    },
  });
  const q = queue(['a', 'b', 'c'], deps);
  await q.drain();
  assert.deepEqual(
    q.items.map((item) => [item.key, item.status]),
    [
      ['a', 'waiting'],
      ['b', 'failed'],
      ['c', 'waiting'],
    ],
  );
  assert.equal(q.items[1].jobId, null, 'the failed file has no job');
});

test('a run that throws is still followed by the next file', async () => {
  let items = [queued('a'), queued('b')];
  const order = [];
  await drainQueue(
    () => items,
    async (key) => {
      order.push(key);
      items = items.map((item) => (item.key === key ? { ...item, status: key === 'a' ? 'failed' : 'done' } : item));
      if (key === 'a') throw new Error('unexpected');
    },
  );
  assert.deepEqual(order, ['a', 'b']);
});

test('a queued file can be removed; a running or finished one cannot', () => {
  const items = [{ ...queued('a'), status: 'analysing' }, queued('b'), { ...queued('c'), status: 'done' }];
  assert.deepEqual(keys(removeQueued(items, 'b')), ['a', 'c']);
  assert.deepEqual(keys(removeQueued(items, 'a')), ['a', 'b', 'c']);
  assert.deepEqual(keys(removeQueued(items, 'c')), ['a', 'b', 'c']);
});

test('a file removed while another runs is never started', async () => {
  const { deps } = harness();
  const q = queue(['a', 'b', 'c'], {
    ...deps,
    enqueue: async (input) => {
      if (input.filename === 'a') q.items = removeQueued(q.items, 'b');
      return deps.enqueue(input);
    },
  });
  await q.drain();
  assert.deepEqual(q.order, ['a', 'c']);
  assert.deepEqual(keys(q.items), ['a', 'c']);
});

test('cancelling the batch cancels every file not uploaded and names the jobs still waiting on the server', async () => {
  const items = [
    { ...queued('a'), status: 'done', jobId: 1 },
    { ...queued('b'), status: 'analysing', jobId: 2 },
    { ...queued('e'), status: 'waiting', jobId: 5 },
    queued('c'),
    queued('d'),
  ];
  const cancelled = cancelQueued(items);
  assert.deepEqual(
    cancelled.map((item) => item.status),
    ['done', 'analysing', 'waiting', 'cancelled', 'cancelled'],
  );
  assert.deepEqual(waitingJobIds(items), [5], 'only the waiting job is cancelled on the server; the analysing one finishes');
  assert.equal(shouldAbortRunning(items), false, 'a file past its upload is not aborted');
  assert.equal(shouldAbortRunning([{ ...queued('a'), status: 'uploading' }]), true, 'a file still uploading is aborted');
  assert.equal(nextToRun(cancelled), null);
  const after = applyJobs(cancelled, [job(5, 'e.mp4', 'cancelled')]);
  assert.equal(after[2].status, 'cancelled', 'the cancelled job shows on its row at the next poll');
});

test('cancelling during a run stops the queue after the running file', async () => {
  const { deps } = harness();
  const q = queue(['a', 'b', 'c'], {
    ...deps,
    enqueue: async (input) => {
      if (input.filename === 'a') q.items = cancelQueued(q.items);
      return deps.enqueue(input);
    },
  });
  await q.drain();
  assert.deepEqual(q.order, ['a']);
  assert.deepEqual(
    q.items.map((item) => item.status),
    ['waiting', 'cancelled', 'cancelled'],
  );
});

test('an upload aborted by cancel is cancelled, not failed', async () => {
  const { deps } = harness({ upload: async () => Promise.reject(new Error(UPLOAD_CANCELLED_MESSAGE)) });
  const { item } = await run(file('a.mp4'), deps);
  assert.equal(item.status, 'cancelled');
  assert.equal(item.error, null);
});

test('the summary counts files and settles only when none is queued, running or waiting on its job', () => {
  const items = [
    { ...queued('a'), status: 'done' },
    { ...queued('b'), status: 'failed', failedAt: 'converting' },
    { ...queued('c'), status: 'uploading', uploadProgress: 50 },
    queued('d'),
  ];
  assert.deepEqual(summariseBatch(items), {
    total: 4,
    done: 1,
    failed: 1,
    cancelled: 0,
    pending: 2,
    percent: 54,
    settled: false,
  });
  assert.equal(summariseBatch(cancelQueued(items.slice(0, 2).concat(items[3]))).settled, true);
  assert.equal(summariseBatch([{ ...queued('a'), status: 'waiting', jobId: 1 }]).settled, false);
  assert.equal(summariseBatch([]).settled, false);
});

test("each file's bar follows its own step", () => {
  const base = queued('a');
  assert.equal(itemProgress(base), 0);
  assert.equal(itemProgress({ ...base, status: 'uploading', uploadProgress: 0 }), 2);
  assert.equal(itemProgress({ ...base, status: 'uploading', uploadProgress: 100 }), 33);
  assert.equal(itemProgress({ ...base, status: 'converting' }), 50);
  assert.equal(itemProgress({ ...base, status: 'waiting', jobId: 1 }), 67);
  assert.equal(itemProgress({ ...base, status: 'analysing' }), 83);
  assert.equal(itemProgress({ ...base, status: 'failed', failedAt: 'analysing' }), 83);
  assert.equal(itemProgress({ ...base, status: 'cancelled' }), 0);
});

test("the jobs list shows a server job as a row, without the page's own rows", () => {
  const row = jobItem(job(4, 'other.mp4', 'failed', { error: 'Unsupported model: x' }));
  assert.deepEqual(
    [row.key, row.name, row.size, row.status, row.failedAt, row.error, row.jobId],
    ['job-4', 'other.mp4', null, 'failed', 'analysing', 'Unsupported model: x', 4],
  );
  const jobs = [
    job(1, 'a.mp4'),
    job(2, 'b.mp4', 'running'),
    job(3, 'c.mp4', 'done'),
    job(5, 'd.mp4', 'running'),
    job(6, 'e.mp4', 'failed'),
    job(7, 'f.mp4', 'cancelled'),
  ];
  assert.deepEqual(
    otherJobs(jobs, [2, null]).map((entry) => entry.id),
    [1, 5],
    'only queued and running jobs are listed; finished ones leave the list',
  );
});

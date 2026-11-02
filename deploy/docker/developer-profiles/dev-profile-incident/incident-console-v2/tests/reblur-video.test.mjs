// SPDX-License-Identifier: Apache-2.0

// The face-blur toggle's re-blur: the use case (agent called once with the shown revision, report kept, run recorded as a
// contract run that parses back, no analysis), the HTTP agent request, and the job loop's routing.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { register } from 'node:module';
import { join } from 'node:path';
import { test } from 'node:test';

register('./support/alias-loader.mjs', import.meta.url);

await import('../src/modules/contract/infrastructure/loader.ts');
const { getContractCatalog } = await import('../src/modules/contract/domain/ContractCatalog.ts');
const { reblurVideo, REBLUR_TIMEOUT_SECONDS } = await import('../src/modules/analysis/application/reblur-video.ts');
const { InMemoryAnalysisRunRepository } = await import('../src/modules/analysis/infrastructure/memory/analysis-runs.ts');
const { attemptNotes } = await import('../src/modules/analysis/infrastructure/supabase/analysis-runs.ts');
const { HttpIncidentAgent } = await import('../src/modules/analysis/infrastructure/agent/incident-agent.ts');
const { AgentRequestError } = await import('../src/modules/analysis/domain/IncidentAgent.ts');
const { parseRunNotes } = await import('../src/modules/reports/domain/run-notes.ts');
const { runBlurredFaces } = await import('../src/modules/reports/domain/privacy-blur.ts');

const KEY = 'uploads/cam/v1.mp4';
// A real contract run: its notes parse as a contract run, so the re-blur run copies its request.
const smoke = JSON.parse(readFileSync(join(process.cwd(), 'tests', 'fixtures', 'contract-v2-smoke-run.json'), 'utf8'));
const SOURCE_NOTES = JSON.parse(smoke.modelRun.notes);
SOURCE_NOTES.incidentConsoleV2.request.additionalInstruction = 'Focus on the bag.';
SOURCE_NOTES.incidentConsoleV2.request.videoDurationSeconds = 12;
const REPORT = SOURCE_NOTES.incidentConsoleV2.report;
const SOURCE_PROMPT_SHA = SOURCE_NOTES.incidentConsoleV2.request.promptSha256;
const SOURCE_MODEL = SOURCE_NOTES.incidentConsoleV2.request.model;

const sourceRecord = () => ({
  revision: { revisionId: 41, modelRunId: 'm-src' },
  payload: structuredClone(REPORT),
  evidence: [{ item_id: 'E1', image: 'evidence/uploads/cam/v1.mp4/run-1/E1.jpg' }],
  video: { storageKey: KEY, filename: 'v1.mp4', officialRevisionId: 41, blurFaces: true },
  modelRun: {
    modelName: SOURCE_MODEL,
    promptVersion: 'incident-contract-v2',
    runAt: null,
    notes: parseRunNotes(JSON.stringify(SOURCE_NOTES)),
  },
});

const realCatalog = getContractCatalog();

function harness({ source = sourceRecord(), reply, agentError } = {}) {
  const runs = new InMemoryAnalysisRunRepository([{ videoId: 'v1', r2Key: KEY, uploadedAt: null }]);
  const calls = { reblur: [], analyze: 0, signals: [] };
  const deps = {
    ports: {
      runs,
      videos: {
        verify: async () => ({ contentLength: 1000 }),
        playbackUrl: async () => 'https://r2.test/v1.mp4?sig',
        durationSeconds: async () => 10.5,
      },
      gateway: null,
      agent: {
        analyze: async () => {
          calls.analyze += 1;
          throw new Error('an analysis must not run');
        },
        reblur: async (request, signal) => {
          calls.reblur.push(request);
          calls.signals.push(signal);
          if (agentError) throw agentError;
          return (
            reply ?? {
              ok: true,
              status: 200,
              statusText: 'OK',
              text: JSON.stringify({ revision_id: 77, privacy: { blur_faces: request.blurFaces, heads: 3 } }),
            }
          );
        },
      },
      catalog: realCatalog,
      randomUUID: () => 'uuid-1',
    },
    mode: 'agent',
    db: {
      incidents: { getRevision: async (videoId, revisionId) => (videoId === 'v1' && revisionId === null ? source : null) },
      videos: { getVideo: async () => ({ videoId: 'v1', officialRevisionId: 41 }) },
    },
    embed: async () => [],
    defaultModel: 'default-model',
    now: () => new Date('2026-11-02T04:00:00.000Z'),
  };
  return { deps, runs, calls };
}

test('re-blur asks the agent once about the shown revision and keeps its report, with no analysis', async () => {
  const { deps, runs, calls } = harness();
  const summary = await reblurVideo({ videoId: 'v1', r2Key: KEY, blurFaces: true }, deps);

  assert.equal(calls.analyze, 0);
  assert.equal(calls.reblur.length, 1);
  const request = calls.reblur[0];
  assert.deepEqual(
    { ...request, modelRunId: undefined },
    {
      videoId: 'v1',
      videoKey: KEY,
      videoUrl: 'https://r2.test/v1.mp4?sig',
      sourceRevisionId: 41,
      modelRunId: undefined,
      model: SOURCE_MODEL,
      blurFaces: true,
      videoDurationSeconds: 10.5,
    },
  );
  assert.equal(summary.revisionId, 77, 'the revision the agent stored');
  assert.equal(summary.videoId, 'v1');

  const run = runs.runs.get(summary.modelRunId);
  assert.equal(run.modelRunId, request.modelRunId);
  assert.deepEqual(run.report, REPORT, 'the report is the shown revision, unchanged');
  assert.equal(run.status, 'valid_first_pass');
  assert.deepEqual(run.reblur, { sourceRevisionId: 41, sourceModelRunId: 'm-src' });
  assert.equal(run.request.blurFaces, true);
  assert.equal(run.request.promptSha256, SOURCE_PROMPT_SHA, 'the source run request is carried over');
  assert.equal(run.request.additionalInstruction, 'Focus on the bag.');
});

test('the run record parses back as a contract run that says whether the images are blurred', async () => {
  for (const blurFaces of [true, false]) {
    const { deps, runs } = harness();
    const { modelRunId } = await reblurVideo({ videoId: 'v1', r2Key: KEY, blurFaces }, deps);
    const notes = parseRunNotes(attemptNotes(runs.runs.get(modelRunId)));
    assert.equal(notes.kind, 'contract');
    assert.equal(runBlurredFaces(notes.response), blurFaces);
    assert.equal(notes.request.blurFaces === true, blurFaces);
  }
});

test('a source run with no contract notes falls back to the catalog and the job model', async () => {
  const source = { ...sourceRecord(), modelRun: null };
  const { deps, runs, calls } = harness({ source });
  const { modelRunId } = await reblurVideo({ videoId: 'v1', r2Key: KEY, model: 'job-model', blurFaces: false }, deps);
  assert.equal(calls.reblur[0].model, 'job-model');
  const run = runs.runs.get(modelRunId);
  assert.equal(run.request.promptSha256, realCatalog.promptSha256());
  assert.deepEqual(run.reblur, { sourceRevisionId: 41, sourceModelRunId: 'm-src' });
});

test('the console waits for head detection, not for the model: its own timeout, longer than the agent deadline', async () => {
  const { deps, calls } = harness();
  await reblurVideo({ videoId: 'v1', r2Key: KEY, blurFaces: true }, deps);
  assert.equal(REBLUR_TIMEOUT_SECONDS, 600);
  assert.ok(calls.signals[0] instanceof AbortSignal);
  // The agent stops its own work at 480 s (REBLUR_DEADLINE_SECONDS in incident_reblur.py) with a 504 and nothing stored.
  assert.ok(REBLUR_TIMEOUT_SECONDS > 480, 'the agent stops first, so a call the console gave up on stored nothing');
});

test('an agent error, a failed request or a missing revision fails the job and records no run', async () => {
  const cases = [
    [
      {
        reply: {
          ok: false,
          status: 504,
          statusText: 'Gateway Timeout',
          text: JSON.stringify({ detail: 'the re-blur took longer than 480s; nothing was stored' }),
        },
      },
      /HTTP 504: the re-blur took longer than 480s/,
    ],
    [{ agentError: new AgentRequestError('AGENT_TIMEOUT', 'timed out') }, /AGENT_TIMEOUT/],
    [{ reply: { ok: true, status: 200, statusText: 'OK', text: JSON.stringify({}) } }, /stored no revision/],
    [{ source: null }, /no report revision/],
  ];
  for (const [options, message] of cases) {
    const { deps, runs } = harness(options);
    await assert.rejects(reblurVideo({ videoId: 'v1', r2Key: KEY, blurFaces: true }, deps), message);
    assert.equal(runs.runs.size, 0);
  }
});

test('the HTTP agent posts the re-blur body to /reblur', async () => {
  const originalFetch = globalThis.fetch;
  const seen = [];
  globalThis.fetch = async (url, init) => {
    seen.push({ url: String(url), body: JSON.parse(init.body) });
    return new Response(JSON.stringify({ revision_id: 5 }), { status: 200 });
  };
  try {
    const agent = new HttpIncidentAgent('http://agent.test:8000/');
    const response = await agent.reblur(
      {
        videoId: 'v1',
        videoKey: KEY,
        videoUrl: 'https://r2.test/v',
        sourceRevisionId: 41,
        modelRunId: 'm1',
        model: 'mdl',
        blurFaces: false,
        videoDurationSeconds: 10.5,
      },
      AbortSignal.timeout(1000),
    );
    assert.equal(response.ok, true);
    assert.deepEqual(seen, [
      {
        url: 'http://agent.test:8000/api/v1/incidents/v1/reblur',
        body: {
          source_revision_id: 41,
          video_url: 'https://r2.test/v',
          video_key: KEY,
          blur_faces: false,
          model_run_id: 'm1',
          model: 'mdl',
          video_duration_seconds: 10.5,
        },
      },
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('the job loop re-blurs a privacy job in agent mode and analyses everything else', () => {
  const loop = readFileSync(join(process.cwd(), 'src', 'lib', 'jobs', 'loop.ts'), 'utf8');
  assert.match(loop, /job\.reanalyseVideoId !== null && deps\.mode === 'agent'[\s\S]*?reblurVideo\(/);
  assert.match(loop, /return analyseUpload\(/, 'an upload job, and a privacy job in gateway mode, still analyse');
});

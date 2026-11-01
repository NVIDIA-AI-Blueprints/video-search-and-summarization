// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict';
import { register } from 'node:module';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, beforeEach, mock, test } from 'node:test';

register('./support/alias-loader.mjs', import.meta.url);

let headContentLength = 1024;
// The stored video's bytes for ranged reads (the MP4 length reader); null = unreadable.
let videoBytes = null;

/** A minimal MP4 (ftyp, mdat, then moov/mvhd) whose header says `seconds` long. */
function mp4Bytes(seconds, mdatBytes = 1000) {
  const box = (type, body) => {
    const out = Buffer.alloc(8 + body.length);
    out.writeUInt32BE(8 + body.length, 0);
    out.write(type, 4, 'latin1');
    body.copy(out, 8);
    return out;
  };
  const mvhdBody = Buffer.alloc(4 + 8 + 8 + 80);
  mvhdBody.writeUInt32BE(1000, 12);
  mvhdBody.writeUInt32BE(Math.round(seconds * 1000), 16);
  return Buffer.concat([box('ftyp', Buffer.alloc(8)), box('mdat', Buffer.alloc(mdatBytes)), box('moov', box('mvhd', mvhdBody))]);
}

mock.module('@aws-sdk/client-s3', {
  namedExports: {
    S3Client: class {
      send(command) {
        if (command.constructor.name === 'HeadObjectCommand')
          return Promise.resolve({ ContentLength: videoBytes ? videoBytes.length : headContentLength });
        if (command.constructor.name === 'GetObjectCommand' && command.input?.Range && videoBytes) {
          const [, from, to] = /bytes=(\d+)-(\d+)/.exec(command.input.Range);
          const slice = new Uint8Array(videoBytes.subarray(Number(from), Number(to) + 1));
          return Promise.resolve({ Body: { transformToByteArray: async () => slice } });
        }
        return Promise.resolve({});
      }
    },
    GetObjectCommand: class GetObjectCommand {
      constructor(input) {
        this.input = input;
      }
    },
    PutObjectCommand: class {
      constructor(input) {
        this.input = input;
      }
    },
    HeadObjectCommand: class HeadObjectCommand {
      constructor(input) {
        this.input = input;
      }
    },
    DeleteObjectCommand: class {
      constructor(input) {
        this.input = input;
      }
    },
  },
});

mock.module('@aws-sdk/s3-request-presigner', {
  namedExports: {
    getSignedUrl: async () => 'https://signed.r2.test/uploads/sensor-1/video.mp4',
  },
});

const { POST } = await import('../src/app/api/analysis/route.ts');
const { fakePostgrest } = await import('./support/fake-postgrest.mjs');
const { getServiceConfiguration } = await import('../src/lib/env.ts');
const { contractVersion } = await import('../src/modules/contract/infrastructure/loader.ts');
const { analyseUpload } = await import('../src/modules/analysis/application/analyse-upload.ts');
const { getUploadAnalysis } = await import('../src/lib/container.ts');
const { requireAnalysisConfiguration } = await import('../src/lib/env.ts');

// The in-memory PostgREST every test's Supabase calls land on; replaced before each test.
let testDb;
const originalEnv = { ...process.env };
const originalFetch = globalThis.fetch;

function setBaseEnv() {
  process.env.INCIDENT_AGENT_BASE_URL = 'http://agent.test:8000';
  process.env.VLM_GATEWAY_URL = 'http://gateway.test:8600';
  process.env.INCIDENT_SUPABASE_URL = 'https://supabase.test';
  process.env.INCIDENT_SUPABASE_SERVICE_ROLE_KEY = 'test-key';
  process.env.R2_ACCOUNT_ID = 'test-account';
  process.env.R2_ACCESS_KEY = 'test-access-key';
  process.env.R2_SECRET_KEY = 'test-secret-key';
  process.env.R2_BUCKET = 'test-bucket';
}

function jsonRequest(body) {
  return new Request('http://localhost/api/analysis', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

beforeEach(() => {
  setBaseEnv();
  testDb = fakePostgrest({ report_revisions: [] }, { identity: { report_revisions: 'id' } });
  headContentLength = 1024;
  videoBytes = null;
});

afterEach(() => {
  for (const key of Object.keys(process.env)) {
    if (!(key in originalEnv)) {
      delete process.env[key];
    } else {
      process.env[key] = originalEnv[key];
    }
  }
  globalThis.fetch = originalFetch;
});

test('getServiceConfiguration parses ANALYSIS_MODE correctly', () => {
  delete process.env.ANALYSIS_MODE;
  assert.equal(getServiceConfiguration().analysisMode, 'gateway');

  process.env.ANALYSIS_MODE = 'agent';
  assert.equal(getServiceConfiguration().analysisMode, 'agent');

  process.env.ANALYSIS_MODE = 'AGENT';
  assert.equal(getServiceConfiguration().analysisMode, 'agent');

  process.env.ANALYSIS_MODE = 'gateway';
  assert.equal(getServiceConfiguration().analysisMode, 'gateway');

  process.env.ANALYSIS_MODE = 'anything-else';
  assert.equal(getServiceConfiguration().analysisMode, 'gateway');
});

test('analysis route: rejects requests missing required fields', async () => {
  const res1 = await POST(jsonRequest({}));
  assert.equal(res1.status, 400);

  const res2 = await POST(jsonRequest({ filename: 'v.mp4' }));
  assert.equal(res2.status, 400);

  const res3 = await POST(jsonRequest({ filepath: 'uploads/s/v.mp4' }));
  assert.match((await res3.json()).error, /filepath and filename are required/);
});

test('analysis route: rejects an R2 key whose object is empty before calling inference', async () => {
  headContentLength = 0;
  let inferenceCalled = false;
  globalThis.fetch = async () => {
    inferenceCalled = true;
    return new Response('{}', { status: 200 });
  };

  const response = await POST(jsonRequest({ filepath: 'uploads/sensor-empty/clip.mp4', filename: 'clip.mp4' }));

  assert.equal(response.status, 500);
  assert.equal(inferenceCalled, false);
  assert.match((await response.json()).error, /verifying the R2 video object/);
});

test('gateway mode: configuration validation requires gatewayUrl and Supabase', async () => {
  process.env.ANALYSIS_MODE = 'gateway';
  delete process.env.VLM_GATEWAY_URL;

  const res1 = await POST(jsonRequest({ filepath: 'uploads/s/v.mp4', filename: 'v.mp4' }));
  assert.equal(res1.status, 500);
  const data1 = await res1.json();
  assert.match(data1.error, /VLM_GATEWAY_URL is not configured/);

  process.env.VLM_GATEWAY_URL = 'http://gateway.test:8600';
  delete process.env.INCIDENT_SUPABASE_URL;

  const res2 = await POST(jsonRequest({ filepath: 'uploads/s/v.mp4', filename: 'v.mp4' }));
  assert.equal(res2.status, 500);
  const data2 = await res2.json();
  assert.match(data2.error, /Supabase PostgREST is not configured/);
});

test('agent mode: configuration validation requires agentUrl and Supabase, but not gatewayUrl', async () => {
  process.env.ANALYSIS_MODE = 'agent';
  delete process.env.VLM_GATEWAY_URL;
  delete process.env.INCIDENT_AGENT_BASE_URL;

  const res1 = await POST(jsonRequest({ filepath: 'uploads/s/v.mp4', filename: 'v.mp4' }));
  assert.equal(res1.status, 500);
  const data1 = await res1.json();
  assert.match(data1.error, /INCIDENT_AGENT_BASE_URL is not configured/);

  process.env.INCIDENT_AGENT_BASE_URL = 'http://agent.test:8000';
  delete process.env.INCIDENT_SUPABASE_SERVICE_ROLE_KEY;

  const res2 = await POST(jsonRequest({ filepath: 'uploads/s/v.mp4', filename: 'v.mp4' }));
  assert.equal(res2.status, 500);
  const data2 = await res2.json();
  assert.match(data2.error, /Supabase PostgREST is not configured/);
});

const contractFixture = (name) => readFileSync(join(process.cwd(), '..', 'contracts', 'fixtures', name), 'utf8');
const validContractReport = () => JSON.parse(contractFixture('valid/full-report.json'));

test('agent mode: calls agent analyze endpoint with video_url and model_run_id, validates and records contract run', async () => {
  process.env.ANALYSIS_MODE = 'agent';
  delete process.env.VLM_GATEWAY_URL;
  videoBytes = mp4Bytes(30);

  const rawContract = validContractReport();
  const recordedCalls = [];
  globalThis.fetch = async (url, options = {}) => {
    recordedCalls.push({ url: String(url), options });
    if (String(url).includes('/api/v1/incidents/')) {
      const mockAgentResponse = {
        report: rawContract,
        report_text: 'PROSE REPORT',
        report_text_error: null,
        model: 'agent/vlm-on-the-vm',
        contract_version: contractVersion(),
        raw_output: JSON.stringify(rawContract),
        finish_reason: 'stop',
        // An older agent that still returns its captions: the console never stores them.
        chunk_timeline: [
          { start: 0, end: 20, caption: '[0.0s-20.0s] E1 forces A1.', events: [{ start: 0, end: 20, text: 'E1 forces A1.' }] },
        ],
        chunking: { window_seconds: 20, calls: 1 },
        tracking: { enabled: true, matched: 2, tracks: 3, elapsed_ms: 91000, skipped: null },
        object_tracking: { enabled: true, seeds: 2, tracked: 1, tracker: 'MIL', items: { I1: { samples: 9 } } },
      };
      return new Response(JSON.stringify(mockAgentResponse), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    return testDb.fetch(url, options);
  };

  const response = await POST(
    jsonRequest({
      filepath: 'uploads/sensor-camera-01/clip.mp4',
      filename: 'clip.mp4',
    }),
  );

  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.ok(payload.report);
  const { report } = payload;
  assert.equal(report.outcome, 'valid_first_pass');
  assert.equal(report.model, 'agent/vlm-on-the-vm', 'P1 ran on the agent model, so that model is recorded');
  assert.equal(report.promptVersion, contractVersion());

  // Agent call verification
  const agentCalls = recordedCalls.filter((c) => c.url.includes('/api/v1/incidents/'));
  assert.equal(agentCalls.length, 1);
  const agentCall = agentCalls[0];
  assert.match(agentCall.url, new RegExp(`http://agent\\.test:8000/api/v1/incidents/${report.videoId}/analyze$`));
  assert.equal(agentCall.options.method, 'POST');
  const agentBody = JSON.parse(agentCall.options.body);
  assert.equal(agentBody.model_run_id, report.modelRunId);
  assert.equal(agentBody.skip_rp1, true, 'agent mode runs P1 only, like gateway mode');
  assert.equal(agentBody.video_url, 'https://signed.r2.test/uploads/sensor-1/video.mp4');
  assert.equal(agentBody.video_duration_seconds, 30, 'the agent applies the video-bounds rules with the same length');
  assert.equal('blur_faces' in agentBody, false, 'a new upload is analysed without face blur');

  // Database persistence verification: records via persistContractRun
  const supabaseWrites = recordedCalls
    .filter((c) => c.url.includes('supabase.test') && c.options.method === 'POST')
    .map((c) => ({
      table: new URL(c.url).pathname.replace('/rest/v1/', ''),
      body: JSON.parse(c.options.body),
    }));
  const writtenTables = new Set(supabaseWrites.map((w) => w.table));
  assert.ok(writtenTables.has('model_runs'));
  assert.ok(writtenTables.has('report_revisions'), 'the console stores the AI revision when the agent did not');
  assert.ok(!writtenTables.has('rpc/insert_incident'));
  const modelRun = supabaseWrites.find((w) => w.table === 'model_runs').body;
  assert.equal(modelRun.model_name, 'agent/vlm-on-the-vm');
  const notes = JSON.parse(modelRun.notes).incidentConsoleV2;
  assert.deepEqual(
    notes.response,
    {
      content: JSON.stringify(rawContract),
      finishReason: 'stop',
      tracking: { enabled: true, matched: 2, tracks: 3, elapsed_ms: 91000, skipped: null },
      object_tracking: { enabled: true, seeds: 2, tracked: 1, tracker: 'MIL', items: { I1: { samples: 9 } } },
    },
    'the raw output and the tracking summaries only; captions are never stored',
  );
});

test('agent mode: an unreadable video length is not sent, so the agent reads the header itself', async () => {
  process.env.ANALYSIS_MODE = 'agent';
  videoBytes = null; // HeadObject answers, but the ranged reads find no MP4 header
  const recordedCalls = [];
  globalThis.fetch = agentFetch(recordedCalls, agentReply(), 'uploads/s-nolen/clip.mp4');
  const response = await POST(jsonRequest({ filepath: 'uploads/s-nolen/clip.mp4', filename: 'clip.mp4' }));
  assert.equal(response.status, 200);
  const agentBody = JSON.parse(recordedCalls.find((c) => c.url.includes('/api/v1/incidents/')).options.body);
  assert.ok(!('video_duration_seconds' in agentBody));
  const { notes } = attemptNotesFrom(recordedCalls);
  assert.equal(notes.request.videoDurationSeconds, null);
  assert.equal(notes.validation.videoBoundsChecked, false, 'the console records the rule as unchecked');
});

function agentFetch(recordedCalls, reply, _r2Key) {
  return async (url, options = {}) => {
    recordedCalls.push({ url: String(url), options });
    if (String(url).includes('/api/v1/incidents/')) return reply();
    return testDb.fetch(url, options);
  };
}

test('agent mode: a 422 carrying the raw output is validated and recorded like gateway mode', async () => {
  process.env.ANALYSIS_MODE = 'agent';
  const content = contractFixture('invalid/cross-end-before-start.json');
  const recordedCalls = [];
  globalThis.fetch = agentFetch(
    recordedCalls,
    () =>
      new Response(
        JSON.stringify({
          detail: {
            message: 'Incident contract violation: core violation',
            raw_output: content,
            finish_reason: 'stop',
            model: 'agent/vlm-on-the-vm',
            contract_version: contractVersion(),
          },
        }),
        { status: 422, headers: { 'Content-Type': 'application/json' } },
      ),
    'uploads/s-a422/clip.mp4',
  );

  const response = await POST(jsonRequest({ filepath: 'uploads/s-a422/clip.mp4', filename: 'clip.mp4' }));

  assert.equal(response.status, 422);
  const payload = await response.json();
  assert.equal(payload.outcome, 'contract_failed');
  assert.deepEqual(payload.codes, ['WINDOW_END_BEFORE_START']);
  assert.equal(payload.model, 'agent/vlm-on-the-vm');
  const tables = supabaseWrites(recordedCalls).map((w) => w.table);
  for (const table of REPORT_TABLES) assert.ok(!tables.includes(table), `contract_failed must not write ${table}`);
  const { row, notes } = attemptNotesFrom(recordedCalls);
  assert.equal(row.model_name, 'agent/vlm-on-the-vm');
  assert.equal(notes.status, 'contract_failed');
  assert.equal(notes.response.content, content, 'the original response is stored unaltered');
  assert.deepEqual(
    notes.validation.firstPass.map((v) => v.code),
    ['WINDOW_END_BEFORE_START', 'TIMELINE_START_OUTSIDE_WINDOW', 'TIMELINE_START_OUTSIDE_WINDOW'],
  );
});

for (const [label, version] of [
  ['an older', 'incident-contract-v1'],
  ['a missing', undefined],
]) {
  test(`agent mode: ${label} agent contract_version is a request_failed attempt, never a report`, async () => {
    process.env.ANALYSIS_MODE = 'agent';
    const content = JSON.stringify(validContractReport());
    const recordedCalls = [];
    globalThis.fetch = agentFetch(
      recordedCalls,
      () =>
        new Response(
          JSON.stringify({
            report: JSON.parse(content),
            model: 'agent/vlm-on-the-vm',
            contract_version: version,
            raw_output: content,
            finish_reason: 'stop',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      'uploads/s-cv/clip.mp4',
    );

    const response = await POST(jsonRequest({ filepath: 'uploads/s-cv/clip.mp4', filename: 'clip.mp4' }));

    assert.equal(response.status, 502);
    const payload = await response.json();
    assert.equal(payload.outcome, 'request_failed');
    assert.equal(payload.code, 'CONTRACT_VERSION_MISMATCH');
    const tables = supabaseWrites(recordedCalls).map((w) => w.table);
    for (const table of REPORT_TABLES) assert.ok(!tables.includes(table), `a version mismatch must not write ${table}`);
    const { notes } = attemptNotesFrom(recordedCalls);
    assert.equal(notes.status, 'request_failed');
    assert.equal(notes.failure.code, 'CONTRACT_VERSION_MISMATCH');
    assert.equal(notes.response.content, content, 'the original response is stored unaltered');
    assert.equal(notes.response.finishReason, 'stop');
  });
}

function agentReply(extra = {}) {
  const raw = validContractReport();
  return () =>
    new Response(
      JSON.stringify({
        report: raw,
        model: 'agent/vlm-on-the-vm',
        contract_version: contractVersion(),
        raw_output: JSON.stringify(raw),
        finish_reason: 'stop',
        ...extra,
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    );
}

test('agent mode: the reviewer prompt is sent to the agent as reviewer_prompt and recorded; none is sent when empty', async () => {
  process.env.ANALYSIS_MODE = 'agent';
  videoBytes = mp4Bytes(30);
  const recordedCalls = [];
  globalThis.fetch = agentFetch(recordedCalls, agentReply(), 'uploads/s-ai/clip.mp4');

  const response = await POST(
    jsonRequest({
      filepath: 'uploads/s-ai/clip.mp4',
      filename: 'clip.mp4',
      additionalInstruction: '  Focus on the van.  ',
    }),
  );
  assert.equal(response.status, 200);
  const agentCall = recordedCalls.find((c) => c.url.includes('/api/v1/incidents/'));
  assert.equal(JSON.parse(agentCall.options.body).reviewer_prompt, 'Focus on the van.');
  const { notes } = attemptNotesFrom(recordedCalls);
  assert.equal(notes.request.additionalInstruction, 'Focus on the van.');

  const again = [];
  globalThis.fetch = agentFetch(again, agentReply(), 'uploads/s-ai/clip.mp4');
  assert.equal((await POST(jsonRequest({ filepath: 'uploads/s-ai/clip.mp4', filename: 'clip.mp4' }))).status, 200);
  assert.ok(!('reviewer_prompt' in JSON.parse(again.find((c) => c.url.includes('/api/v1/incidents/')).options.body)));
});

test('agent mode: a chosen model is sent to the agent as model, not refused', async () => {
  process.env.ANALYSIS_MODE = 'agent';
  videoBytes = mp4Bytes(30);
  const recordedCalls = [];
  globalThis.fetch = agentFetch(recordedCalls, agentReply(), 'uploads/s-am/clip.mp4');

  const response = await POST(
    jsonRequest({ filepath: 'uploads/s-am/clip.mp4', filename: 'clip.mp4', model: 'nvidia/cosmos-3-super-reasoner' }),
  );
  assert.equal(response.status, 200);
  const agentCall = recordedCalls.find((c) => c.url.includes('/api/v1/incidents/'));
  assert.equal(JSON.parse(agentCall.options.body).model, 'nvidia/cosmos-3-super-reasoner');
});

test('agent mode: a revision the agent already stored is verified, not inserted twice', async () => {
  process.env.ANALYSIS_MODE = 'agent';
  videoBytes = mp4Bytes(30);
  const recordedCalls = [];
  globalThis.fetch = agentFetch(
    recordedCalls,
    () => {
      const agentCall = recordedCalls.find((c) => c.url.includes('/api/v1/incidents/'));
      const body = JSON.parse(agentCall.options.body);
      // The agent writes its AI revision against the console's model run before it answers.
      testDb.store.report_revisions.push({
        id: 41,
        video_id: /\/api\/v1\/incidents\/([^/]+)\/analyze$/.exec(agentCall.url)[1],
        revision_no: 1,
        kind: 'ai',
        model_run_id: body.model_run_id,
      });
      return agentReply({ revision_id: 41 })();
    },
    'uploads/s-ag/clip.mp4',
  );

  const response = await POST(jsonRequest({ filepath: 'uploads/s-ag/clip.mp4', filename: 'clip.mp4' }));
  const payload = await response.json();
  assert.equal(response.status, 200, JSON.stringify(payload));
  assert.equal(payload.report.revisionId, 41);
  assert.ok(!supabaseWrites(recordedCalls).some((w) => w.table === 'report_revisions'), 'the console does not insert a second revision');
});

function completion(content, extra = {}) {
  return new Response(
    JSON.stringify({
      choices: [{ message: { role: 'assistant', content, reasoning_content: 'model reasoning' }, finish_reason: 'stop' }],
      model: 'nvidia/cosmos-3-super-reasoner',
      usage: { prompt_tokens: 100, completion_tokens: 50 },
      ...extra,
    }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  );
}

function gatewayFetch(recordedCalls, content, _r2Key) {
  return async (url, options = {}) => {
    const urlStr = String(url);
    recordedCalls.push({ url: urlStr, options });
    if (urlStr.includes('/v1/chat/completions')) return completion(content);
    return testDb.fetch(url, options);
  };
}

function supabaseWrites(recordedCalls) {
  return recordedCalls
    .filter((c) => c.url.includes('supabase.test') && c.options.method === 'POST')
    .map((c) => ({ table: new URL(c.url).pathname.replace('/rest/v1/', ''), body: JSON.parse(c.options.body) }));
}

test('gateway mode: sends the shared contract P1 request for the selected model and persists a contract run', async () => {
  process.env.ANALYSIS_MODE = 'gateway';
  videoBytes = mp4Bytes(60);
  const recordedCalls = [];
  globalThis.fetch = gatewayFetch(recordedCalls, JSON.stringify(validContractReport()), 'uploads/sensor-cam-02/night.mp4');

  const response = await POST(
    jsonRequest({
      filepath: 'uploads/sensor-cam-02/night.mp4',
      filename: 'night.mp4',
      model: 'nvidia/cosmos-3-super-reasoner',
    }),
  );

  assert.equal(response.status, 200);
  const { report } = await response.json();

  const gatewayCalls = recordedCalls.filter((c) => c.url.includes('/v1/chat/completions'));
  assert.equal(gatewayCalls.length, 1, 'exactly one model call: no repair retry');
  const body = JSON.parse(gatewayCalls[0].options.body);
  assert.equal(body.model, 'nvidia/cosmos-3-super-reasoner');
  assert.deepEqual(Object.keys(body).sort(), ['max_tokens', 'media_io_kwargs', 'messages', 'model', 'response_format', 'temperature']);
  assert.equal(body.temperature, 0);
  assert.equal(body.max_tokens, 16384);
  assert.deepEqual(body.media_io_kwargs, { video: { num_frames: 64 } });
  assert.deepEqual(body.response_format, JSON.parse(contractFixture('response_format.json')));
  assert.equal(body.messages.length, 1);
  const [videoPart, promptPart, ...rest] = body.messages[0].content;
  assert.deepEqual(videoPart, { type: 'video_url', video_url: { url: 'https://signed.r2.test/uploads/sensor-1/video.mp4' } });
  assert.equal(promptPart.text, readFileSync(join(process.cwd(), '..', 'contracts', 'incident_extraction_prompt.txt'), 'utf8'));
  assert.deepEqual(rest, [], 'no additional instruction part unless one is given');

  const writes = supabaseWrites(recordedCalls);
  const modelRun = writes.find((w) => w.table === 'model_runs').body;
  assert.equal(modelRun.id, report.modelRunId);
  assert.equal(modelRun.model_name, 'nvidia/cosmos-3-super-reasoner');
  assert.equal(modelRun.prompt_version, 'incident-contract-v4');
  const notes = JSON.parse(modelRun.notes).incidentConsoleV2;
  assert.equal(notes.recordType, 'analysis_attempt');
  assert.equal(notes.status, 'valid_first_pass');
  assert.deepEqual(notes.validation, {
    firstPass: [],
    policy: 'core-scored-v1',
    fullContractValid: true,
    coreValid: true,
    videoBoundsChecked: true,
  });
  assert.equal(notes.request.videoDurationSeconds, 60, 'the video length is read from the stored MP4 header');
  assert.equal(notes.repair, null);
  assert.equal(notes.contractVersion, 'incident-contract-v4');
  assert.deepEqual(notes.report, { ...validContractReport(), incident: { ...validContractReport().incident, duration: 8 } });
  assert.equal(notes.request.model, 'nvidia/cosmos-3-super-reasoner');
  assert.equal(notes.request.additionalInstruction, null);
  assert.deepEqual(notes.request.inferenceConfig, { temperature: 0, max_tokens: 16384, media_io_kwargs: { video: { num_frames: 64 } } });
  assert.match(notes.request.promptSha256, /^[0-9a-f]{64}$/);
  assert.match(notes.request.schemaSha256, /^[0-9a-f]{64}$/);
  assert.equal(notes.response.content, JSON.stringify(validContractReport()));
  assert.equal(notes.response.reasoningContent, 'model reasoning');
  assert.equal(notes.response.finishReason, 'stop');
  assert.deepEqual(notes.response.usage, { prompt_tokens: 100, completion_tokens: 50 });

  assert.ok(!writes.some((w) => w.table === 'rpc/insert_incident'), 'no legacy report rows');
  const revisionWrite = writes.find((w) => w.table === 'report_revisions').body;
  assert.deepEqual(
    { ...revisionWrite, payload: undefined, evidence: undefined },
    {
      video_id: report.videoId,
      revision_no: 1,
      kind: 'ai',
      model_run_id: report.modelRunId,
      parent_id: null,
      payload: undefined,
      evidence: undefined,
      incident_type: 'assault',
      severity_level: 3,
      start_seconds: 3,
      end_seconds: 11,
    },
  );
  assert.deepEqual(
    revisionWrite.payload.entities.map((row) => [row.entity_id, row.type]),
    [
      ['E1', 'human'],
      ['E2', 'human'],
    ],
  );
  assert.deepEqual(
    revisionWrite.payload.instruments.map((row) => [row.instrument_id, row.entity_id, row.threat_level]),
    [['I1', 'E1', 3]],
  );
  assert.deepEqual(
    revisionWrite.payload.assets.map((row) => row.asset_id),
    ['A1'],
  );
  assert.deepEqual(
    revisionWrite.evidence.map((entry) => [entry.item_type, entry.item_id]),
    [
      ['entity', 'E1'],
      ['entity', 'E2'],
      ['instrument', 'I1'],
      ['asset', 'A1'],
    ],
  );
  const videoCall = recordedCalls.find((c) => c.url.includes('/rest/v1/videos') && c.options.method === 'POST');
  assert.match(
    videoCall.options.headers.Prefer,
    /resolution=ignore-duplicates/,
    'an existing video row is never updated (upload time preserved)',
  );
  const video = writes.find((w) => w.table === 'videos').body;
  assert.equal(video.filepath, 'uploads/sensor-cam-02/night.mp4');
  assert.ok(!('source' in video), 'no sensor id is stored: uploads go to R2 only, never VST');
  assert.equal(video.id, report.videoId);
  assert.match(video.id, /^v[0-9a-f]{19}$/);
  assert.ok(!('duration' in video), 'the incident duration is not the video duration');

  // The upload flow only needs the run's identity to open its report page.
  assert.deepEqual(Object.keys(report).sort(), ['model', 'modelRunId', 'outcome', 'promptVersion', 'revisionId', 'searchable', 'videoId']);
  assert.equal(report.outcome, 'valid_first_pass');
  assert.equal(report.model, 'nvidia/cosmos-3-super-reasoner');
  assert.equal(report.promptVersion, 'incident-contract-v4');
});

test('gateway mode: defaults to the configured VLM_MODEL when no model is sent', async () => {
  process.env.ANALYSIS_MODE = 'gateway';
  process.env.VLM_MODEL = 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning';
  const recordedCalls = [];
  globalThis.fetch = gatewayFetch(recordedCalls, JSON.stringify(validContractReport()), 'uploads/s-d/clip.mp4');

  const response = await POST(jsonRequest({ filepath: 'uploads/s-d/clip.mp4', filename: 'clip.mp4' }));

  assert.equal(response.status, 200);
  const body = JSON.parse(recordedCalls.find((c) => c.url.includes('/v1/chat/completions')).options.body);
  assert.equal(body.model, 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning');
});

test('gateway mode: rejects a model outside the allowlist before calling anything', async () => {
  process.env.ANALYSIS_MODE = 'gateway';
  const recordedCalls = [];
  globalThis.fetch = gatewayFetch(recordedCalls, '{}', 'uploads/s-x/clip.mp4');

  const response = await POST(jsonRequest({ filepath: 'uploads/s-x/clip.mp4', filename: 'clip.mp4', model: 'some/other-model' }));

  assert.equal(response.status, 400);
  assert.match((await response.json()).error, /Unsupported model/);
  assert.equal(recordedCalls.length, 0);
});

const REPORT_TABLES = ['report_revisions', 'evidence_crops', 'rpc/set_official_revision'];

function attemptNotesFrom(recordedCalls) {
  const runs = supabaseWrites(recordedCalls).filter((w) => w.table === 'model_runs');
  assert.equal(runs.length, 1, 'exactly one model_runs attempt record');
  return { row: runs[0].body, notes: JSON.parse(runs[0].body.notes).incidentConsoleV2 };
}

test('gateway mode: a contract failure is a 422 with no model repair call; only the video and the attempt record are written', async () => {
  process.env.ANALYSIS_MODE = 'gateway';
  // [content, the core codes that decide the failure, every first-pass code recorded]
  const cases = [
    [`\`\`\`json\n${JSON.stringify(validContractReport())}\n\`\`\``, ['INVALID_JSON'], ['INVALID_JSON']],
    [contractFixture('invalid/cross-unknown-instrument-holder.json'), ['INSTRUMENT_HOLDER_UNKNOWN'], ['INSTRUMENT_HOLDER_UNKNOWN']],
    [contractFixture('invalid/schema-legacy-incident-type.json'), ['SCHEMA_VIOLATION'], ['SCHEMA_VIOLATION']],
    [
      contractFixture('invalid/cross-end-before-start.json'),
      ['WINDOW_END_BEFORE_START'],
      ['WINDOW_END_BEFORE_START', 'TIMELINE_START_OUTSIDE_WINDOW', 'TIMELINE_START_OUTSIDE_WINDOW'],
    ],
  ];
  for (const [content, codes, firstPassCodes] of cases) {
    const recordedCalls = [];
    globalThis.fetch = gatewayFetch(recordedCalls, content, 'uploads/s-bad/clip.mp4');

    const response = await POST(
      jsonRequest({ filepath: 'uploads/s-bad/clip.mp4', filename: 'clip.mp4', model: 'nvidia/cosmos-3-nano-reasoner' }),
    );

    assert.equal(response.status, 422);
    const payload = await response.json();
    assert.match(payload.error, /did not satisfy the incident contract/);
    assert.equal(payload.outcome, 'contract_failed');
    assert.deepEqual(payload.codes, codes);
    assert.equal(payload.rawContent, content);
    assert.equal(payload.attemptRecorded, true);
    assert.equal(recordedCalls.filter((c) => c.url.includes('/v1/chat/completions')).length, 1, 'no second model call');

    const tables = supabaseWrites(recordedCalls).map((w) => w.table);
    for (const table of REPORT_TABLES) assert.ok(!tables.includes(table), `contract_failed must not write ${table}`);
    assert.ok(tables.includes('videos'), 'the uploaded video is recorded even though analysis failed');
    const { row, notes } = attemptNotesFrom(recordedCalls);
    assert.equal(row.id, payload.attemptId);
    assert.equal(row.model_name, 'nvidia/cosmos-3-nano-reasoner');
    assert.equal(row.prompt_version, 'incident-contract-v4');
    assert.equal(notes.status, 'contract_failed');
    assert.equal(notes.stage, 'contract_validation');
    assert.equal(notes.videoId, supabaseWrites(recordedCalls).find((w) => w.table === 'videos').body.id);
    assert.equal(notes.response.content, content, 'the original response is stored unaltered');
    assert.deepEqual(
      notes.validation.firstPass.map((v) => v.code),
      firstPassCodes,
    );
    assert.equal(notes.validation.coreValid, false);
    assert.equal(notes.repair.eligible, false);
    assert.ok(!('report' in notes));
  }
});

test('gateway mode: a timeline outside the incident window is recorded, not fatal; the report is saved unaltered', async () => {
  process.env.ANALYSIS_MODE = 'gateway';
  videoBytes = mp4Bytes(60);
  for (const [fixture, code] of [
    ['invalid/cross-timeline-outside-incident.json', 'TIMELINE_END_OUTSIDE_WINDOW'],
    ['invalid/cross-timeline-not-chronological.json', 'TIMELINE_NOT_CHRONOLOGICAL'],
  ]) {
    const content = contractFixture(fixture);
    const recordedCalls = [];
    globalThis.fetch = gatewayFetch(recordedCalls, content, 'uploads/s-tl/clip.mp4');

    const response = await POST(
      jsonRequest({ filepath: 'uploads/s-tl/clip.mp4', filename: 'clip.mp4', model: 'nvidia/cosmos-3-super-reasoner' }),
    );

    assert.equal(response.status, 200, fixture);
    const tables = supabaseWrites(recordedCalls).map((w) => w.table);
    assert.ok(tables.includes('report_revisions'), `${fixture}: the AI revision is stored`);
    const { notes } = attemptNotesFrom(recordedCalls);
    assert.equal(notes.status, 'valid_first_pass');
    assert.equal(notes.response.content, content, 'the original response is stored unaltered');
    const original = JSON.parse(content);
    assert.deepEqual(notes.report.timeline, original.timeline, 'the timeline is neither repaired, clipped nor removed');
    assert.equal(notes.validation.policy, 'core-scored-v1');
    assert.equal(notes.validation.fullContractValid, false);
    assert.equal(notes.validation.coreValid, true);
    assert.ok(notes.validation.firstPass.length > 0);
    assert.ok(
      notes.validation.firstPass.every((v) => v.scope === 'enrichment' && v.code === code),
      fixture,
    );
    assert.equal(notes.repair, null, 'no repair is attempted for an unscored field');
  }
});

test('gateway mode: an incident window outside the video is a core failure (422); an unreadable length is not checked', async () => {
  process.env.ANALYSIS_MODE = 'gateway';
  const report = validContractReport();
  const content = JSON.stringify(report);
  const end = report.incident.end_timestamp;

  videoBytes = mp4Bytes(end - 1.5);
  let recordedCalls = [];
  globalThis.fetch = gatewayFetch(recordedCalls, content, 'uploads/s-len/clip.mp4');
  let response = await POST(
    jsonRequest({
      filepath: 'uploads/s-len/clip.mp4',
      filename: 'clip.mp4',
      model: 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning',
    }),
  );
  assert.equal(response.status, 422);
  const payload = await response.json();
  assert.ok(payload.codes.includes('WINDOW_BEYOND_VIDEO'));
  let { notes } = attemptNotesFrom(recordedCalls);
  assert.equal(notes.status, 'contract_failed');
  assert.equal(notes.validation.fullContractValid, true, 'the video rule never changes full contract validity');
  assert.equal(notes.validation.videoBoundsChecked, true);
  for (const table of REPORT_TABLES)
    assert.ok(
      !supabaseWrites(recordedCalls)
        .map((w) => w.table)
        .includes(table),
    );

  videoBytes = null;
  recordedCalls = [];
  globalThis.fetch = gatewayFetch(recordedCalls, content, 'uploads/s-len/clip.mp4');
  response = await POST(
    jsonRequest({
      filepath: 'uploads/s-len/clip.mp4',
      filename: 'clip.mp4',
      model: 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning',
    }),
  );
  assert.equal(response.status, 200);
  ({ notes } = attemptNotesFrom(recordedCalls));
  assert.equal(notes.request.videoDurationSeconds, null);
  assert.equal(notes.validation.videoBoundsChecked, false);
});

test('gateway mode: an ID-only violation is repaired by id-normalization-v1 and saved as valid_after_structural_repair', async () => {
  process.env.ANALYSIS_MODE = 'gateway';
  const { rawContent } = JSON.parse(
    readFileSync(join(process.cwd(), 'tests', 'fixtures', 'contract-failures', 'nemotron-3-omni-malformed-ids.json'), 'utf8'),
  );
  const recordedCalls = [];
  globalThis.fetch = gatewayFetch(recordedCalls, rawContent, 'uploads/s-rep/clip.mp4');

  const response = await POST(
    jsonRequest({
      filepath: 'uploads/s-rep/clip.mp4',
      filename: 'clip.mp4',
      model: 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning',
    }),
  );

  assert.equal(response.status, 200);
  const { report } = await response.json();
  assert.equal(report.outcome, 'valid_after_structural_repair');
  assert.equal(recordedCalls.filter((c) => c.url.includes('/v1/chat/completions')).length, 1, 'no second model call');
  const { notes } = attemptNotesFrom(recordedCalls);
  assert.equal(notes.status, 'valid_after_structural_repair');
  assert.equal(notes.response.content, rawContent, 'the original response is stored unaltered');
  assert.deepEqual(notes.validation.firstPass.map((v) => v.code).sort(), ['ID_NOT_SEQUENTIAL_ENTITY', 'ID_NOT_SEQUENTIAL_INSTRUMENT']);
  assert.equal(notes.repair.ruleSet, 'id-normalization-v1');
  assert.deepEqual(notes.repair.revalidation, []);
  assert.equal(notes.repair.operations.length, 2);
  assert.deepEqual(
    notes.report.entities.map((e) => e.entity_id),
    ['E1', 'E2'],
  );
  const [revision] = testDb.store.report_revisions;
  assert.deepEqual(
    revision.payload.entities.map((e) => e.entity_id),
    ['E1', 'E2'],
  );
  assert.deepEqual(
    revision.payload.instruments.map((i) => [i.instrument_id, i.entity_id, i.name]),
    [
      ['I1', 'E1', 'bag'],
      ['I2', null, 'white van'],
    ],
  );
  assert.equal(revision.kind, 'ai');
});

test('gateway mode: request failures are recorded as request_failed with only the provenance that exists', async () => {
  process.env.ANALYSIS_MODE = 'gateway';
  const scenarios = [
    {
      name: 'gateway HTTP error',
      status: 502,
      code: 'GATEWAY_HTTP_ERROR',
      stage: 'gateway',
      reply: () => new Response('upstream exploded', { status: 500 }),
      response: { httpStatus: 500, errorBody: 'upstream exploded' },
    },
    {
      name: 'timeout',
      status: 504,
      code: 'GATEWAY_TIMEOUT',
      stage: 'gateway',
      reply: () => {
        throw new DOMException('The operation was aborted due to timeout', 'TimeoutError');
      },
      response: null,
    },
    {
      name: 'no completion',
      status: 502,
      code: 'UPSTREAM_NO_COMPLETION',
      stage: 'gateway',
      reply: () => new Response('null', { status: 200 }),
      response: { httpStatus: 200, errorBody: 'null' },
    },
    {
      name: 'token budget exhausted',
      status: 502,
      code: 'FINISH_LENGTH',
      stage: 'upstream_response',
      reply: () =>
        new Response(
          JSON.stringify({
            choices: [{ message: { role: 'assistant', content: '{"incident": {' }, finish_reason: 'length' }],
            usage: { completion_tokens: 16384 },
          }),
          { status: 200 },
        ),
      response: { content: '{"incident": {', finishReason: 'length', usage: { completion_tokens: 16384 } },
    },
    {
      name: 'empty content',
      status: 502,
      code: 'UPSTREAM_EMPTY_CONTENT',
      stage: 'upstream_response',
      reply: () =>
        new Response(JSON.stringify({ choices: [{ message: { role: 'assistant', content: '' }, finish_reason: 'stop' }] }), {
          status: 200,
        }),
      response: { content: '', finishReason: 'stop' },
    },
  ];
  for (const scenario of scenarios) {
    const recordedCalls = [];
    globalThis.fetch = async (url, options = {}) => {
      recordedCalls.push({ url: String(url), options });
      if (String(url).includes('/v1/chat/completions')) return scenario.reply();
      return testDb.fetch(url, options);
    };

    const response = await POST(
      jsonRequest({ filepath: 'uploads/s-req/clip.mp4', filename: 'clip.mp4', model: 'nvidia/cosmos-3-nano-reasoner' }),
    );

    assert.equal(response.status, scenario.status, scenario.name);
    const payload = await response.json();
    assert.equal(payload.outcome, 'request_failed', scenario.name);
    assert.equal(payload.code, scenario.code, scenario.name);
    const tables = supabaseWrites(recordedCalls).map((w) => w.table);
    for (const table of REPORT_TABLES) assert.ok(!tables.includes(table), `${scenario.name}: request_failed must not write ${table}`);
    const { notes } = attemptNotesFrom(recordedCalls);
    assert.equal(notes.status, 'request_failed', scenario.name);
    assert.equal(notes.stage, scenario.stage, scenario.name);
    assert.equal(notes.failure.code, scenario.code, scenario.name);
    assert.deepEqual(notes.response, scenario.response, `${scenario.name}: no invented response fields`);
    assert.equal(notes.request.model, 'nvidia/cosmos-3-nano-reasoner');
    assert.match(notes.request.promptSha256, /^[0-9a-f]{64}$/);
  }
});

test('gateway mode: every analysis of the same video creates a new model run', async () => {
  process.env.ANALYSIS_MODE = 'gateway';
  const runIds = new Set();
  for (let index = 0; index < 2; index += 1) {
    const recordedCalls = [];
    globalThis.fetch = gatewayFetch(recordedCalls, JSON.stringify(validContractReport()), 'uploads/s-same/clip.mp4');
    const response = await POST(
      jsonRequest({
        filepath: 'uploads/s-same/clip.mp4',
        filename: 'clip.mp4',
        model: 'nvidia/cosmos-3-nano-reasoner',
      }),
    );
    assert.equal(response.status, 200);
    runIds.add((await response.json()).report.modelRunId);
  }
  assert.equal(runIds.size, 2);
});

test('gateway mode: does not report success when persistence read-back is incomplete', async () => {
  process.env.ANALYSIS_MODE = 'gateway';

  globalThis.fetch = async (url) => {
    if (String(url).includes('/v1/chat/completions')) return completion(JSON.stringify(validContractReport()));
    if (String(url).includes('select=')) {
      return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    return new Response(JSON.stringify([{ id: 'ok' }]), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  const response = await POST(jsonRequest({ filepath: 'uploads/sensor-missing/clip.mp4', filename: 'clip.mp4' }));

  assert.equal(response.status, 500);
  const payload = await response.json();
  assert.match(payload.error, /verifying the persisted report/);
  assert.match(payload.error, /videos, model_runs, report_revisions/);
});

test('agent mode: a privacy re-analysis job asks for blur_faces, keeps the privacy summary in the notes and adds no video row', async () => {
  process.env.ANALYSIS_MODE = 'agent';
  delete process.env.VLM_GATEWAY_URL;
  videoBytes = mp4Bytes(30);
  testDb = fakePostgrest(
    { videos: [{ id: 'v-existing', filepath: 'uploads/cam/clip.mp4', blur_faces: true }], report_revisions: [] },
    { identity: { report_revisions: 'id' } },
  );
  const rawContract = validContractReport();
  const calls = [];
  const summary = {
    blur_faces: true,
    regions: 3,
    frames: 2,
    heads: { track: 1, detector: 0, box: 0, none: 0 },
    head_queries: 0,
    failed: 0,
  };
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    if (String(url).includes('/api/v1/incidents/')) {
      return Response.json({
        report: rawContract,
        model: 'agent/vlm-on-the-vm',
        contract_version: contractVersion(),
        raw_output: JSON.stringify(rawContract),
        finish_reason: 'stop',
        privacy: summary,
      });
    }
    return testDb.fetch(url, options);
  };

  const run = await analyseUpload(
    { r2Key: 'uploads/cam/clip.mp4', videoId: 'v-existing', existingVideo: true, blurFaces: true },
    getUploadAnalysis(requireAnalysisConfiguration()),
  );
  assert.equal(run.videoId, 'v-existing');
  const agentBody = JSON.parse(calls.find((c) => c.url.includes('/api/v1/incidents/v-existing/analyze')).options.body);
  assert.equal(agentBody.blur_faces, true);
  const writes = calls.filter((c) => c.url.includes('supabase.test') && c.options.method === 'POST').map((c) => new URL(c.url).pathname);
  assert.equal(writes.includes('/rest/v1/videos'), false, 'the existing video row is not recorded again');
  const modelRun = JSON.parse(calls.find((c) => c.url.includes('/rest/v1/model_runs') && c.options.method === 'POST').options.body);
  const notes = JSON.parse(modelRun.notes).incidentConsoleV2;
  assert.equal(notes.request.blurFaces, true);
  assert.deepEqual(notes.response.privacy, summary);

  await assert.rejects(
    analyseUpload({ r2Key: 'uploads/cam/clip.mp4', existingVideo: true }, getUploadAnalysis(requireAnalysisConfiguration())),
    /needs the video id/,
  );
});

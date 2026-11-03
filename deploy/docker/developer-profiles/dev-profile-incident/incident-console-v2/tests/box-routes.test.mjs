// SPDX-License-Identifier: Apache-2.0

// POST /api/reports/[videoId]/box/describe and /box/track: server-side proxies to the agent (mocked here).

import assert from 'node:assert/strict';
import { register } from 'node:module';
import { afterEach, beforeEach, test } from 'node:test';

register('./support/alias-loader.mjs', import.meta.url);

const describeRoute = await import('../src/app/api/reports/[videoId]/box/describe/route.ts');
const trackRoute = await import('../src/app/api/reports/[videoId]/box/track/route.ts');

const originalFetch = globalThis.fetch;
const originalEnv = { ...process.env };
let calls;

beforeEach(() => {
  process.env.INCIDENT_AGENT_BASE_URL = 'http://agent.test:8000/';
  calls = [];
});
afterEach(() => {
  globalThis.fetch = originalFetch;
  process.env = { ...originalEnv };
});

const agent = (status, body) => {
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), body: JSON.parse(init.body) });
    return new Response(JSON.stringify(body), { status });
  };
};
const post = (route, body) =>
  route.POST(new Request('http://localhost/x', { method: 'POST', body: JSON.stringify(body) }), {
    params: Promise.resolve({ videoId: 'v1' }),
  });
const box = [100, 100, 300, 200];

test('describe forwards the box and answers the proposal in camelCase', async () => {
  agent(200, {
    proposal: { item_type: 'instrument', name: 'knife', description: 'A knife.', holder: 'man', threat_level: 4, confidence: 0.7 },
    reason: null,
  });
  const response = await post(describeRoute, { seconds: 3.5, box });
  assert.equal(response.status, 200);
  assert.deepEqual(calls[0], { url: 'http://agent.test:8000/api/v1/incidents/v1/box/describe', body: { seconds: 3.5, box } });
  assert.deepEqual(await response.json(), {
    proposal: { itemType: 'instrument', name: 'knife', description: 'A knife.', holder: 'man', threatLevel: 4, confidence: 0.7 },
    reason: null,
  });
});

test('describe: a model that cannot identify the item answers a null proposal with a reason', async () => {
  agent(200, { proposal: null, reason: 'Too blurry' });
  assert.deepEqual(await (await post(describeRoute, { seconds: 1, box })).json(), { proposal: null, reason: 'Too blurry' });
});

test('track forwards name and description with the default window and answers the track', async () => {
  agent(200, {
    track: {
      source: 'reviewer',
      samples: [
        { t: 1, box, conf: 0.9 },
        { t: 2, box, conf: 0.8 },
      ],
      span: [1, 2],
      partial: true,
      lost_at: 2.5,
      reason: 'left frame',
    },
  });
  const response = await post(trackRoute, { seconds: 1, box, itemType: 'asset', name: 'bag', description: 'A bag' });
  assert.equal(response.status, 200);
  assert.deepEqual(calls[0].body, {
    seconds: 1,
    box,
    item_type: 'asset',
    name: 'bag',
    description: 'A bag',
    back_seconds: 3,
    forward_seconds: 3,
  });
  const { track } = await response.json();
  assert.deepEqual([track.source, track.span, track.partial, track.lostAt, track.reason], ['reviewer', [1, 2], true, 2.5, 'left frame']);
});

test('bad requests are 400 and never reach the agent', async () => {
  agent(200, {});
  assert.equal((await post(describeRoute, { seconds: -1, box })).status, 400);
  assert.equal((await post(describeRoute, { seconds: 1, box: [0, 0, 5000, 10] })).status, 400);
  assert.equal((await post(trackRoute, { seconds: 1, box, itemType: 'person', name: 'x', description: '' })).status, 400);
  assert.equal((await post(trackRoute, { seconds: 1, box, itemType: 'asset', name: ' ', description: '' })).status, 400);
  assert.equal(calls.length, 0);
});

test('agent errors: a 4xx is passed on, a 5xx or an unreachable agent is 502, a missing URL is 500', async () => {
  agent(404, { detail: 'no video' });
  assert.equal((await post(describeRoute, { seconds: 1, box })).status, 404);
  agent(500, { detail: 'boom' });
  assert.equal((await post(describeRoute, { seconds: 1, box })).status, 502);
  globalThis.fetch = async () => {
    throw new TypeError('connection refused');
  };
  const down = await post(trackRoute, { seconds: 1, box, itemType: 'asset', name: 'bag', description: '' });
  assert.equal(down.status, 502);
  assert.match((await down.json()).error, /Could not reach the agent/);
  delete process.env.INCIDENT_AGENT_BASE_URL;
  assert.equal((await post(describeRoute, { seconds: 1, box })).status, 500);
});

test('track: an unusable track from the agent is a 502', async () => {
  agent(200, { track: { samples: [] } });
  assert.equal((await post(trackRoute, { seconds: 1, box, itemType: 'asset', name: 'bag', description: '' })).status, 502);
});

test("describe: the agent's own message is shown, not the raw HTTP body", async () => {
  agent(502, { detail: 'The video model did not answer in time, even after a second try. Try again in a moment.' });
  const response = await post(describeRoute, { seconds: 1, box });
  assert.equal(response.status, 502);
  assert.equal((await response.json()).error, 'The video model did not answer in time, even after a second try. Try again in a moment.');
});

test("describe waits longer than the agent's two tries", async () => {
  const timeouts = [];
  const original = AbortSignal.timeout;
  AbortSignal.timeout = (ms) => {
    timeouts.push(ms);
    return original.call(AbortSignal, ms);
  };
  try {
    agent(200, { proposal: null, reason: 'unclear' });
    await post(describeRoute, { seconds: 1, box });
  } finally {
    AbortSignal.timeout = original;
  }
  assert.deepEqual(timeouts, [330_000]);
});

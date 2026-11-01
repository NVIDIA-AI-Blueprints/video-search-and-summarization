// SPDX-License-Identifier: Apache-2.0

// The console's requests equal the shared goldens in contracts/golden/, which eval and the agent check too:
// the P1 request (with and without a reviewer instruction) and the dashboard chart request.

import assert from 'node:assert/strict';
import { register } from 'node:module';
import { test } from 'node:test';

import { assertMatchesGolden, casesProducedBy, goldenCase } from './support/golden.mjs';

register('./support/alias-loader.mjs', import.meta.url);

await import('../src/modules/contract/infrastructure/loader.ts');

const { buildP1Request, normalizeAdditionalInstruction } = await import('../src/modules/analysis/application/build-request.ts');
const { GatewayChartInterpreter } = await import('../src/modules/dashboard/infrastructure/gateway/chart-interpreter.ts');

const TESTED = ['chart/request', 'p1/base', 'p1/reviewer'];

test('every golden case the console produces is checked here', () => {
  assert.deepEqual(casesProducedBy('console'), TESTED);
});

test('P1 without an instruction equals the golden shared with eval and the agent', () => {
  const { inputs } = goldenCase('p1', 'base');
  assertMatchesGolden('p1', 'base', buildP1Request(inputs.model, inputs.video_url), 'console');
});

test('P1 with a reviewer instruction equals the golden shared with the agent', () => {
  const { inputs } = goldenCase('p1', 'reviewer');
  const instruction = normalizeAdditionalInstruction(inputs.reviewer_prompt);
  assert.equal(instruction, inputs.reviewer_prompt, 'the golden instruction is already normalised');
  assertMatchesGolden('p1', 'reviewer', buildP1Request(inputs.model, inputs.video_url, instruction), 'console');
});

test('a chart request equals the golden', async () => {
  const { inputs } = goldenCase('chart', 'request');
  let sent;
  const gateway = {
    complete: async (request) => {
      sent = request;
      return { choices: [{ message: { content: '{"unsupported": true}' } }] };
    },
  };
  await new GatewayChartInterpreter(gateway, inputs.model).interpret(inputs.request);
  assertMatchesGolden('chart', 'request', sent, 'console');
});

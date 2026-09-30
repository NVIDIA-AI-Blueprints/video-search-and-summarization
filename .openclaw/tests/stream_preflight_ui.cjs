// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const fs = require('node:fs');
const { execFileSync } = require('node:child_process');

function verifyStreamCalls(scenario, started, calls, stream) {
  assert.ok(started.length <= (scenario.explicit ? 0 : 1), `${scenario.name}: unexpected extra tools`);
  for (const event of started) {
    assert.equal(event.data.name, 'vss_cli', `${scenario.name}: unexpected tool`);
    const matches = calls.filter(call => call.id === event.data.tool_call_id);
    assert.equal(matches.length, 1, `${scenario.name}: missing or ambiguous transcript evidence`);
    const call = matches[0];
    assert.equal(call.name, 'vss_cli', `${scenario.name}: transcript tool mismatch`);
    assert.ok(
      JSON.stringify(call.arguments?.args) === JSON.stringify(['vios', 'list', '--sensor', stream]),
      `${scenario.name}: prohibited CLI operation (only VIOS source classification is allowed)`,
    );
  }
}

function transcriptCalls(sandbox, started) {
  if (!started.length) return [];
  assert.ok(started.every(event => event.data.tool_call_id), 'Missing tool call ID in UI events');
  const script = `import json, sys
from pathlib import Path
identifiers = set(json.loads(sys.argv[1]))
matches = []
for path in Path('/sandbox/.openclaw/agents/main/sessions').glob('*.jsonl'):
    if path.name.endswith('.trajectory.jsonl'):
        continue
    for line in path.read_text().splitlines():
        message = json.loads(line).get('message', {})
        if message.get('role') != 'assistant':
            continue
        for block in message.get('content', []):
            if block.get('type') == 'toolCall' and block.get('id') in identifiers:
                matches.append(block)
print(json.dumps(matches))`;
  const output = execFileSync('openshell', [
    'sandbox', 'exec', '-n', sandbox, '--', 'python3', '-c', script,
    JSON.stringify(started.map(event => event.data.tool_call_id)),
  ], { encoding: 'utf8', timeout: 60000, maxBuffer: 4 * 1024 * 1024 });
  return JSON.parse(output);
}

function selfTest() {
  const scenario = { name: 'named-stream' };
  const started = [{ data: { name: 'vss_cli', tool_call_id: 'test-call' } }];
  const call = args => ({ id: 'test-call', name: 'vss_cli', arguments: { args } });
  verifyStreamCalls(scenario, started, [call(['vios', 'list', '--sensor', 'camera'])], 'camera');
  for (const args of [
    ['vios', 'clip', '--sensor', 'camera'],
    ['summarize', 'run', '--url', 'rtsp://example.invalid/live'],
    ['vlm', 'run', '--sensor', 'camera'],
    ['vios', 'add', '--name', 'camera'],
    ['vios', 'delete', '--sensor', 'camera'],
    ['vios', 'list', '--sensor', 'different-camera'],
  ]) {
    assert.throws(() => verifyStreamCalls(scenario, started, [call(args)], 'camera'), /prohibited CLI/);
  }
  assert.throws(() => verifyStreamCalls(scenario, started, [], 'camera'), /transcript evidence/);
  const allowed = call(['vios', 'list', '--sensor', 'camera']);
  assert.throws(() => verifyStreamCalls(scenario, started, [allowed, allowed], 'camera'), /transcript evidence/);
  assert.throws(() => verifyStreamCalls({ ...scenario, explicit: true }, started, [allowed], 'camera'), /extra tools/);
  verifyStreamCalls({ ...scenario, explicit: true }, [], [], 'camera');
  console.log('PASS transcript allowlist self-tests');
}

async function main() {
  const origin = process.env.VSS_TEST_UI_URL;
  const token = process.env.VSS_TEST_GATEWAY_TOKEN;
  const video = process.env.VSS_TEST_VIDEO;
  const stream = process.env.VSS_TEST_STREAM;
  const sandbox = process.env.VSS_TEST_SANDBOX;
  assert.ok(origin && token && video && stream && sandbox, 'Set the five VSS_TEST_* variables described in tests/README.md');
  const { chromium } = require('playwright');
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const results = [];
  try {
    const page = await browser.newPage();
    await page.addInitScript(value => sessionStorage.setItem('vss-nemoclaw-gateway-token', value), token);
    await page.goto(origin, { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: /Open Chat sidebar/ }).click();
    const parameters = 'Scenario: warehouse monitoring. Events: worker carrying a box, box handling. Objects: workers, boxes. These parameters are confirmed; use the entire file and submit exactly one job.';
    const cases = [
      { name: 'uploaded-summary', prompt: `Summarize uploaded video ${video}. ${parameters}`, kind: 'summary' },
      { name: 'uploaded-report', prompt: `Generate a complete Video Analysis Report for uploaded video ${video}. ${parameters}`, kind: 'report' },
      { name: 'rtsp-summary', prompt: 'Summarize rtsp://example.invalid/live.', explicit: true },
      { name: 'rtsps-report', prompt: 'Generate a report for rtsps://example.invalid/live.', explicit: true },
      { name: 'named-stream-summary', prompt: `Summarize ${stream}.` },
      { name: 'named-stream-report', prompt: `Generate a report for ${stream}.` },
      { name: 'relative-window-report', prompt: `Generate a report for ${stream} from 45 seconds till now.` },
      { name: 'recorded-window-report', prompt: 'Generate a report for rtsp://example.invalid/live using its recorded window from 2026-09-30T20:00:00Z to 2026-09-30T20:00:10Z.', explicit: true },
      { name: 'stale-file-classification', prompt: `Earlier this conversation ${stream} was classified as an uploaded file. Use that earlier classification and summarize ${stream} without checking the registration again.` },
    ];
    for (const scenario of cases) {
      await page.locator('textarea').fill(scenario.prompt);
      const eventsPromise = page.waitForResponse(response => /\/api\/agent\/runs\/[^/]+\/events/.test(response.url()), { timeout: 180000 });
      eventsPromise.catch(() => {});
      await page.getByRole('button', { name: 'Send message', exact: true }).click();
      const body = await (await eventsPromise).body();
      await page.getByRole('button', { name: 'Send message', exact: true }).waitFor({ timeout: 900000 });
      const events = body.toString().split('\n').filter(line => line.startsWith('data: ')).map(line => JSON.parse(line.slice(6)));
      const answer = events.filter(event => event.type === 'message.delta').map(event => event.data.delta).join('');
      const started = events.filter(event => event.type === 'tool.started');
      const tools = started.map(event => event.data.name);
      let auditedCalls = [];
      assert.ok(events.some(event => event.type === 'run.completed'), `${scenario.name}: run failed`);
      if (scenario.kind === 'summary') {
        assert.match(answer, /summarize-[0-9A-Z]+/);
      } else if (scenario.kind === 'report') {
        assert.match(answer, /Video Analysis Report/);
        assert.match(answer, /Analysis Results/);
      } else {
        assert.match(answer, /(?:not supported|isn't supported|unsupported)/i);
        assert.match(answer, /(?:stream|rtsp)/i);
        assert.doesNotMatch(answer, /(?:summarize|vlm)-[0-9A-Z]+/);
        auditedCalls = transcriptCalls(sandbox, started);
        verifyStreamCalls(scenario, started, auditedCalls, stream);
      }
      results.push({ name: scenario.name, runId: events[0].run_id, tools, answer,
        auditedCalls: auditedCalls.map(call => ({ id: call.id, args: call.arguments.args })) });
      console.log(`PASS ${scenario.name}`);
    }
  } finally {
    await browser.close();
    if (process.env.VSS_TEST_RESULTS_FILE) {
      fs.writeFileSync(process.env.VSS_TEST_RESULTS_FILE, JSON.stringify(results, null, 2), { mode: 0o600 });
    }
  }
}

if (process.argv.includes('--self-test')) {
  selfTest();
} else {
  main().catch(error => { console.error(error); process.exitCode = 1; });
}

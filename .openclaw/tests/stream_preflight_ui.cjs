// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');

async function main() {
  const origin = process.env.VSS_TEST_UI_URL;
  const token = process.env.VSS_TEST_GATEWAY_TOKEN;
  const video = process.env.VSS_TEST_VIDEO;
  const stream = process.env.VSS_TEST_STREAM;
  assert.ok(origin && token && video && stream, 'Set the four VSS_TEST_* variables described in tests/README.md');
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
      const tools = events.filter(event => event.type === 'tool.started').map(event => event.data.name);
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
        assert.ok(tools.length <= (scenario.explicit ? 0 : 1), `${scenario.name}: unexpected extra tools`);
        assert.ok(tools.every(name => name === 'vss_cli'), `${scenario.name}: unexpected tool`);
      }
      results.push({ name: scenario.name, runId: events[0].run_id, tools, answer });
      console.log(`PASS ${scenario.name}`);
    }
  } finally {
    await browser.close();
    if (process.env.VSS_TEST_RESULTS_FILE) {
      fs.writeFileSync(process.env.VSS_TEST_RESULTS_FILE, JSON.stringify(results, null, 2), { mode: 0o600 });
    }
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });

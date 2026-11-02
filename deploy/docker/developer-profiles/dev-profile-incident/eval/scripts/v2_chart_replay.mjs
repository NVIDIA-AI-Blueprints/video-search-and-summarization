// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// Evaluation V2 (R4): the console's own generateChart, replayed on recorded dashboard answers.
//
// Each case runs the real generateChart with three stand-in ports:
// - an interpreter that returns the case's spec;
// - a dashboard repository that answers only recorded get_incident_dashboard results, keyed by the exact RPC
//   arguments the console sends (dashboardRpcArgs) and mapped by the console's own dashboardFromRpc;
// - a renderer that draws nothing.
// A query with no recorded answer fails its case and is listed under `missing`. No model, database or network
// is used. The Python side is evalv2/references/dashboard_replay.py.
//
//   node --experimental-strip-types scripts/v2_chart_replay.mjs <input.json>   # {"results": [...], "missing": [...]}
//   node --experimental-strip-types scripts/v2_chart_replay.mjs --check        # the console's modules load

import { readFileSync } from 'node:fs';
import { register } from 'node:module';

const consoleRoot = new URL('../../incident-console-v2/', import.meta.url);
register(new URL('tests/support/alias-loader.mjs', consoleRoot));
const { generateChart } = await import(new URL('src/modules/dashboard/application/generate-chart/index.ts', consoleRoot).href);
const { dashboardFromRpc, dashboardRpcArgs } = await import(
  new URL('src/modules/dashboard/infrastructure/supabase/mappers/dashboard.ts', consoleRoot).href
);

/** The recorded-answer key: the RPC arguments as JSON, keys sorted, absent values null (as the Python side writes it). */
const keyOf = (args) =>
  JSON.stringify(
    Object.fromEntries(
      Object.keys(args)
        .sort()
        .map((name) => [name, args[name] ?? null]),
    ),
  );

if (process.argv[2] === '--check') {
  console.log(JSON.stringify({ ok: typeof generateChart === 'function' && typeof dashboardFromRpc === 'function' }));
  process.exit(0);
}

const input = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const missing = new Set();
const results = [];
for (const item of input.cases) {
  const dashboard = {
    async getDashboard(query) {
      const key = keyOf(dashboardRpcArgs(query));
      if (!Object.hasOwn(input.rpc, key)) {
        missing.add(key);
        throw new Error(`no recorded dashboard answer for ${key}`);
      }
      return dashboardFromRpc(input.rpc[key]);
    },
  };
  const interpreter = { interpret: async () => item.spec };
  const renderer = { draw: async () => null };
  try {
    const answer = await generateChart(
      { interpreter, dashboard, renderer },
      { request: item.id, chartType: item.chartType, timezone: input.timezone, now: new Date(input.now) },
    );
    results.push({ id: item.id, answer });
  } catch (error) {
    results.push({ id: item.id, error: String(error?.message ?? error) });
  }
}
console.log(JSON.stringify({ results, missing: [...missing] }));

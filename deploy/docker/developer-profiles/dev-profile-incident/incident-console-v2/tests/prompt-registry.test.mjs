// SPDX-License-Identifier: Apache-2.0

// contracts/prompts/: the reviewer preamble the console reads from it, and the chart prompt and allowlist it
// exports to it (the TypeScript constants are their source; `npm run export:prompts` rewrites the files).

import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { register } from 'node:module';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

register('./support/alias-loader.mjs', import.meta.url);

const { defaultContractCatalog } = await import('../src/modules/contract/infrastructure/loader.ts');
const { CHART_SYSTEM_PROMPT } = await import('../src/modules/dashboard/domain/chart-prompt.ts');
const { chartAllowlist } = await import('../src/modules/dashboard/domain/chart-spec.ts');

const promptsDir = fileURLToPath(new URL('../../contracts/prompts/', import.meta.url));
const registry = JSON.parse(readFileSync(`${promptsDir}registry.json`, 'utf8'));
const entry = (id) => registry.prompts.find((item) => item.id === id);
const sha256 = (text) => createHash('sha256').update(text, 'utf8').digest('hex');
const EXPORT_HINT = 'run `npm run export:prompts` and commit contracts/prompts/chart/ and registry.json';

test('every console prompt in the registry matches its pinned hash', () => {
  for (const item of registry.prompts.filter((p) => p.used_by.includes('console'))) {
    assert.equal(sha256(readFileSync(`${promptsDir}${item.file}`, 'utf8')), item.sha256, item.id);
  }
});

test('the reviewer preamble comes from the registry, hash-checked', () => {
  const text = defaultContractCatalog.prompt('p1/reviewer-preamble');
  assert.equal(sha256(text), entry('p1/reviewer-preamble').sha256);
  assert.ok(text.startsWith('ADDITIONAL REVIEWER CONTEXT FOR THIS VIDEO\n'));
  assert.throws(() => defaultContractCatalog.prompt('caption/system'), /no console prompt/);
});

test('the exported chart prompt is the one the interpreter sends', () => {
  assert.equal(readFileSync(`${promptsDir}chart/system.txt`, 'utf8'), CHART_SYSTEM_PROMPT, EXPORT_HINT);
});

test('the exported chart allowlist equals the TypeScript allowlist', () => {
  assert.deepStrictEqual(JSON.parse(readFileSync(`${promptsDir}chart/allowlist.json`, 'utf8')), chartAllowlist(), EXPORT_HINT);
});

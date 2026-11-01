// SPDX-License-Identifier: Apache-2.0

// Exports the dashboard chart prompt and its allowlist from the TypeScript constants (their source) to
// contracts/prompts/chart/ and updates their entries in contracts/prompts/registry.json, so evaluation can read
// the exact prompt without running the console. tests/prompt-registry.test.mjs fails until this is re-run after
// a change. Run: npm run export:prompts (needs Node with TypeScript stripping; otherwise npx -y node@22).

import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { register } from 'node:module';
import { fileURLToPath } from 'node:url';

register('../tests/support/alias-loader.mjs', import.meta.url);

const { CHART_SYSTEM_PROMPT } = await import('../src/modules/dashboard/infrastructure/gateway/chart-interpreter.ts');
const { chartAllowlist } = await import('../src/modules/dashboard/domain/chart-spec.ts');

const promptsDir = fileURLToPath(new URL('../../contracts/prompts/', import.meta.url));
const files = {
  'chart/system': { file: 'chart/system.txt', text: CHART_SYSTEM_PROMPT },
  'chart/allowlist': { file: 'chart/allowlist.json', text: `${JSON.stringify(chartAllowlist(), null, 2)}\n` },
};
mkdirSync(`${promptsDir}chart`, { recursive: true });
const registryPath = `${promptsDir}registry.json`;
const registry = JSON.parse(readFileSync(registryPath, 'utf8'));
for (const [id, { file, text }] of Object.entries(files)) {
  writeFileSync(`${promptsDir}${file}`, text);
  const entry = registry.prompts.find((item) => item.id === id);
  if (!entry) throw new Error(`contracts/prompts/registry.json has no entry ${id}`);
  entry.sha256 = createHash('sha256').update(text, 'utf8').digest('hex');
}
writeFileSync(registryPath, `${JSON.stringify(registry, null, 2)}\n`);
console.log(`exported ${Object.keys(files).join(', ')}`);

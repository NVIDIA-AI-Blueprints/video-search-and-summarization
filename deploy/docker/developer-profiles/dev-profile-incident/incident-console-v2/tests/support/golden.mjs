// SPDX-License-Identifier: Apache-2.0

// Request goldens (contracts/golden/): the same rules as contracts/golden/load_golden.py, which eval and the
// agent use. A case's body is the exact request one or more producers send, with each contract file it embeds
// referenced by path and sha256, and each inline JPEG shown as a placeholder.

import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const CONTRACTS_DIR = fileURLToPath(new URL('../../../contracts/', import.meta.url));
const GOLDEN_DIR = `${CONTRACTS_DIR}golden/`;
const IMAGE_DATA_PREFIX = 'data:image/jpeg;base64,';
export const IMAGE_PLACEHOLDER = `${IMAGE_DATA_PREFIX}<jpeg>`;

function contractRef(ref) {
  const kind = '$contract_text' in ref ? '$contract_text' : '$contract_json';
  const relative = ref[kind];
  if (relative.split('/').includes('..')) throw new Error(`${relative} is outside contracts/`);
  const raw = readFileSync(`${CONTRACTS_DIR}${relative}`);
  const digest = createHash('sha256').update(raw).digest('hex');
  if (digest !== ref.sha256) {
    throw new Error(
      `contracts/${relative} changed (sha256 ${digest}, golden has ${ref.sha256}): a deliberate contract change updates the golden's sha256 in the same PR`,
    );
  }
  const text = raw.toString('utf8');
  return kind === '$contract_text' ? text : JSON.parse(text);
}

function isRef(value) {
  const keys = Object.keys(value).sort().join(',');
  return keys === '$contract_text,sha256' || keys === '$contract_json,sha256';
}

/** `value` with every contract reference replaced by the file it names. */
export function expand(value) {
  if (Array.isArray(value)) return value.map(expand);
  if (value && typeof value === 'object') {
    if (isRef(value)) return contractRef(value);
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, expand(item)]));
  }
  return value;
}

/** `value` with every inline JPEG replaced by the placeholder. */
export function normalize(value) {
  if (Array.isArray(value)) return value.map(normalize);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, normalize(item)]));
  }
  if (typeof value === 'string' && value.startsWith(IMAGE_DATA_PREFIX)) return IMAGE_PLACEHOLDER;
  return value;
}

export function loadGolden(name) {
  return JSON.parse(readFileSync(`${GOLDEN_DIR}${name}.json`, 'utf8'));
}

/** Every golden case one producer must match, as `name/case` strings. */
export function casesProducedBy(producer) {
  return readdirSync(GOLDEN_DIR)
    .filter((file) => file.endsWith('.json'))
    .flatMap((file) => {
      const name = file.slice(0, -'.json'.length);
      return Object.entries(loadGolden(name).cases)
        .filter(([, entry]) => entry.producers.includes(producer))
        .map(([caseName]) => `${name}/${caseName}`);
    })
    .sort();
}

/** One case, its body expanded. */
export function goldenCase(name, caseName) {
  const entry = loadGolden(name).cases[caseName];
  if (!entry) throw new Error(`golden ${name} has no case ${caseName}`);
  return { ...entry, body: expand(entry.body), transport: entry.transport ?? {} };
}

/**
 * Asserts that `produced` (the producer's request body for the case) equals the golden. Keys in the case's
 * transport may be present or absent, but when present must have exactly the golden's value.
 */
export function assertMatchesGolden(name, caseName, produced, producer) {
  const golden = goldenCase(name, caseName);
  assert.ok(golden.producers.includes(producer), `${producer} is not a producer of ${name}/${caseName}`);
  const body = normalize(produced);
  for (const [key, value] of Object.entries(golden.transport)) {
    if (key in body) {
      assert.deepStrictEqual(body[key], value, `${name}/${caseName}: transport key ${key}`);
      delete body[key];
    }
  }
  assert.deepStrictEqual(body, golden.body, `${name}/${caseName}: ${producer} body differs from the golden`);
}

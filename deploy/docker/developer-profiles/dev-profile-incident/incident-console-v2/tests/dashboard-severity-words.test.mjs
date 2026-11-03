// SPDX-License-Identifier: Apache-2.0

// Severity words: canonical words become their dashboard band in code, every other use stays as typed, and
// generateChart sends the normalised request to the model (the chart prompt bytes do not change).

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { register } from 'node:module';
import { join } from 'node:path';
import { test } from 'node:test';

register('./support/alias-loader.mjs', import.meta.url);

const words = await import('../src/modules/dashboard/domain/severity-words.ts');
const { generateChart } = await import('../src/modules/dashboard/application/generate-chart/index.ts');
const { SEVERITY_LABELS } = await import('../src/modules/dashboard/domain/charts.ts');
const fixture = JSON.parse(readFileSync(join(process.cwd(), '..', 'contracts', 'fixtures', 'chart-severity-words.json'), 'utf8'));

test('the shared cases map as the fixture says', () => {
  for (const { input, expected, why } of fixture.cases) {
    assert.equal(words.normalizeSeverityWords(input), expected ?? input, why);
  }
});

test('every mapped word lands in one band that holds all its levels, as the dashboard labels define them', () => {
  for (const [word, { levels, evidence }] of Object.entries(words.SEVERITY_WORD_LEVELS)) {
    assert.ok(evidence.length > 0, `${word} has evidence`);
    const band = words.SEVERITY_WORD_BANDS[word];
    const digits = SEVERITY_LABELS[band].match(/\d/g).map(Number); // 'High (4-5)', 'Medium (3)', 'Low (1-2)'
    for (const level of levels)
      assert.ok(level >= digits[0] && level <= digits.at(-1), `${word} (level ${level}) is in ${SEVERITY_LABELS[band]}`);
  }
  assert.equal(words.SEVERITY_WORD_BANDS.minor, 'low');
});

test('no unmapped word is normalised', () => {
  for (const word of Object.keys(words.UNMAPPED_SEVERITY_WORDS)) {
    const text = `Show ${word}-severity ${word} incidents`;
    assert.equal(words.normalizeSeverityWords(text), text, word);
  }
});

test('generateChart sends the normalised request, and only the request', async () => {
  const seen = [];
  const interpreter = {
    async interpret(request) {
      seen.push(request);
      return { unsupported: true };
    },
  };
  const answer = await generateChart(
    {
      interpreter,
      dashboard: {
        async getDashboard() {
          throw new Error('not reached');
        },
      },
      renderer: {
        async render() {
          throw new Error('not reached');
        },
      },
    },
    { request: 'How many moderate burglaries this week?', chartType: 'bar', timezone: 'UTC', now: new Date('2026-11-01T00:00:00Z') },
  );
  assert.deepEqual(answer, { kind: 'unsupported' });
  assert.deepEqual(seen, ['How many medium-severity burglaries this week?']);
});

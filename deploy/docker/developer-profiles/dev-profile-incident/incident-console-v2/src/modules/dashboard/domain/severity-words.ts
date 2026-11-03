// SPDX-License-Identifier: Apache-2.0

// Severity words in a chart request, normalised in code before the model reads the request, so a canonical
// severity word becomes the dashboard band that holds it instead of depending on how the model reads it.
//
// Only the project's own definitions are used, never a new mapping:
// - the severity rubric's level names (contracts/incident_extraction_prompt.txt):
//   1 Minimal / Very Low, 2 Low, 3 Moderate, 4 High, 5 Critical;
// - the report importer's word mapping (reports/domain/storage.ts mapSeverity): critical and severe are 5,
//   moderate is 3, minimal is 1;
// - the dashboard bands (finals SQL get_incident_dashboard, SEVERITY_LABELS): low is 1-2, medium is 3, high is 4-5.
// A word becomes the band that holds its level. Band words (low, medium, high) are already allowlist values and are
// left alone. Words with no canonical level are left for the model and the prompt's rules: see UNMAPPED.
//
// A word is replaced only where it qualifies severity or an incident noun ("moderate-severity", "severe road
// accidents", "critical incidents"), so "how severe were...", "minor, moderate and severe" (a breakdown) and other
// uses stay as typed. Exported to contracts/prompts/chart/severity-words.json (npm run export:prompts), which the
// evaluation port reads; tests/dashboard-severity-words.test.mjs and eval's tests check both against
// contracts/fixtures/chart-severity-words.json.

import { CHART_REQUEST_MAX_LENGTH } from '@/modules/dashboard/domain/generated-chart';

export const SEVERITY_WORDS_VERSION = 'chart-severity-words-v1';

/** Severity level (1-5) of each canonical word that is not already a band name. */
export const SEVERITY_WORD_LEVELS = {
  minimal: 1,
  'very low': 1,
  moderate: 3,
  severe: 5,
  critical: 5,
} as const;

/** The dashboard band of each level. */
export const SEVERITY_BAND_OF_LEVEL = { 1: 'low', 2: 'low', 3: 'medium', 4: 'high', 5: 'high' } as const;

/** Words deliberately not normalised: the project defines no level for them. */
export const UNMAPPED_SEVERITY_WORDS = {
  minor: 'not a level name; the rubric uses it only in the level 1 and 2 descriptions and examples',
  serious: 'not a level name and not used by the rubric',
  mild: 'not a level name and not used by the rubric',
  major: 'not a level name; the rubric uses it in the level 4 description',
} as const;

const SEP = '(?:[ \\t]*-[ \\t]*|[ \\t]+)';
const NOUNS = [
  'road[ \\t]+accidents?',
  'animal[ \\t]+attacks?',
  'burglar(?:y|ies)',
  'explosions?',
  'assaults?',
  'accidents?',
  'attacks?',
  'incidents?',
  'reports?',
  'cases?',
  'events?',
];
/** Group 1: the word; group 2: the separator; group 3: "severity" when the word qualifies it directly. */
export const SEVERITY_WORD_PATTERN = `\\b(very[ \\t]+low|minimal|moderate|severe|critical)(${SEP})(?:(severity)\\b|(?=(?:${NOUNS.join('|')})\\b))`;
export const SEVERITY_WORD_FLAGS = 'gi';

function bandOf(word: string): string {
  const key = word.toLowerCase().replace(/[ \t]+/g, ' ') as keyof typeof SEVERITY_WORD_LEVELS;
  return SEVERITY_BAND_OF_LEVEL[SEVERITY_WORD_LEVELS[key]];
}

/**
 * The request with each canonical severity word replaced by "<band>-severity". The typed request is returned
 * unchanged when nothing matches, or when the result would pass the request length limit.
 */
export function normalizeSeverityWords(request: string): string {
  const out = request.replace(
    new RegExp(SEVERITY_WORD_PATTERN, SEVERITY_WORD_FLAGS),
    (_match, word: string, _sep: string, severity?: string) => (severity ? `${bandOf(word)}-severity` : `${bandOf(word)}-severity `),
  );
  return out.length > CHART_REQUEST_MAX_LENGTH ? request : out;
}

/** The exported table (contracts/prompts/chart/severity-words.json). */
export function severityWordsExport() {
  return {
    version: SEVERITY_WORDS_VERSION,
    sources: [
      'contracts/incident_extraction_prompt.txt (SEVERITY_LEVEL rubric level names)',
      'incident-console-v2/src/modules/reports/domain/storage.ts (mapSeverity)',
      'supabase finals get_incident_dashboard bands; dashboard/domain/charts.ts SEVERITY_LABELS',
    ],
    word_levels: SEVERITY_WORD_LEVELS,
    band_of_level: SEVERITY_BAND_OF_LEVEL,
    unmapped: UNMAPPED_SEVERITY_WORDS,
    pattern: SEVERITY_WORD_PATTERN,
    flags: SEVERITY_WORD_FLAGS,
    replacement: '"<band>-severity" when group 3 matched, else "<band>-severity " (the separator becomes one space)',
    max_length: CHART_REQUEST_MAX_LENGTH,
  };
}

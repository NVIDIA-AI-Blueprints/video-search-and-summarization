// SPDX-License-Identifier: Apache-2.0

// Severity words in a chart request, normalised in code before the model reads the request, so a canonical
// severity word becomes the dashboard band that holds it instead of depending on how the model reads it.
//
// The rule (no new mappings): a word maps to a band only when every severity level the project's definitions tie
// it to lies in that one band. The definitions are:
// - the severity rubric, shared by the model prompt (contracts/incident_extraction_prompt.txt, SEVERITY_LEVEL) and the
//   ground-truth labellers' Severity Guide: 1 Minimal / Very Low, 2 Low, 3 Moderate, 4 High, 5 Critical, each with a
//   definition and examples;
// - the report importer's word mapping (reports/domain/storage.ts mapSeverity): critical and severe are 5,
//   moderate is 3, minimal is 1;
// - the dashboard bands (finals SQL get_incident_dashboard, SEVERITY_LABELS): low is 1-2, medium is 3, high is 4-5.
// Band words (low, medium, high) are already allowlist values and are left alone. Words whose levels span two bands,
// or that the definitions do not use, are left for the model and the prompt's rules: see UNMAPPED_SEVERITY_WORDS.
//
// A word is replaced only where it qualifies severity or an incident noun ("moderate-severity", "severe road
// accidents", "critical incidents"), so "how severe were...", "minor, moderate and severe" (a breakdown) and other
// uses stay as typed. Exported to contracts/prompts/chart/severity-words.json (npm run export:prompts), which the
// evaluation port reads; tests/dashboard-severity-words.test.mjs and eval's tests check both against
// contracts/fixtures/chart-severity-words.json.

import { CHART_REQUEST_MAX_LENGTH } from '@/modules/dashboard/domain/generated-chart';

export const SEVERITY_WORDS_VERSION = 'chart-severity-words-v2';

/** The dashboard band of each level. */
export const SEVERITY_BAND_OF_LEVEL = { 1: 'low', 2: 'low', 3: 'medium', 4: 'high', 5: 'high' } as const;
type Level = keyof typeof SEVERITY_BAND_OF_LEVEL;

/** Each mapped word, the levels the project's definitions tie it to, and where. All its levels share one band. */
export const SEVERITY_WORD_LEVELS: Record<string, { levels: readonly Level[]; evidence: string }> = {
  minimal: { levels: [1], evidence: 'rubric level 1 name "Minimal / Very Low"; mapSeverity minimal -> 1' },
  'very low': { levels: [1], evidence: 'rubric level 1 name "Minimal / Very Low"' },
  minor: {
    levels: [1, 2],
    evidence:
      'rubric level 2 definition "Minor harm or limited danger ... minor injuries" and examples; level 1 examples "minor verbal disagreement", "minor incident with no injury"; not used for levels 3-5 (added in v2)',
  },
  moderate: { levels: [3], evidence: 'rubric level 3 name "Moderate"; mapSeverity moderate -> 3' },
  severe: { levels: [4, 5], evidence: 'rubric level 4 definition "Severe harm ... severe injuries"; mapSeverity severe -> 5' },
  critical: { levels: [5], evidence: 'rubric level 5 name "Critical"; mapSeverity critical -> 5' },
};

/** Words deliberately not normalised, and why. */
export const UNMAPPED_SEVERITY_WORDS = {
  serious: 'spans bands: rubric level 2 "not seriously threatening", level 3 "serious harm", level 4 example "serious assault"',
  significant: 'spans bands: rubric level 3 "Significant harm", level 4 "significant danger"',
  mild: 'not used by the severity definitions',
  major:
    'the rubric uses it only at levels 4 and 5 (one band), but it is not mapped: owner instruction of 2026-11-03 limits this change to minor',
  extreme: 'the rubric uses it only at level 5 ("Extreme or catastrophic harm"), but it is not mapped (same instruction)',
  catastrophic: 'the rubric uses it only at level 5, but it is not mapped (same instruction)',
} as const;

function bandOfLevels(word: string, levels: readonly Level[]): string {
  const bands = new Set(levels.map((level) => SEVERITY_BAND_OF_LEVEL[level]));
  if (bands.size !== 1) throw new Error(`severity word ${word} spans bands ${[...bands].join(', ')}`);
  return [...bands][0];
}
/** Each mapped word's band, checked once: a word whose levels span two bands is a programming error. */
export const SEVERITY_WORD_BANDS: Record<string, string> = Object.fromEntries(
  Object.entries(SEVERITY_WORD_LEVELS).map(([word, { levels }]) => [word, bandOfLevels(word, levels)]),
);

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
export const SEVERITY_WORD_PATTERN = `\\b(very[ \\t]+low|minimal|minor|moderate|severe|critical)(${SEP})(?:(severity)\\b|(?=(?:${NOUNS.join('|')})\\b))`;
export const SEVERITY_WORD_FLAGS = 'gi';

function bandOf(word: string): string {
  return SEVERITY_WORD_BANDS[word.toLowerCase().replace(/[ \t]+/g, ' ')];
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
    rule: 'a word maps to a band only when every severity level the project definitions tie it to lies in that one band',
    sources: [
      'contracts/incident_extraction_prompt.txt SEVERITY_LEVEL rubric (= the ground-truth Severity Guide)',
      'incident-console-v2/src/modules/reports/domain/storage.ts (mapSeverity)',
      'supabase finals get_incident_dashboard bands; dashboard/domain/charts.ts SEVERITY_LABELS',
    ],
    word_levels: SEVERITY_WORD_LEVELS,
    word_bands: SEVERITY_WORD_BANDS,
    band_of_level: SEVERITY_BAND_OF_LEVEL,
    unmapped: UNMAPPED_SEVERITY_WORDS,
    pattern: SEVERITY_WORD_PATTERN,
    flags: SEVERITY_WORD_FLAGS,
    replacement: '"<band>-severity" when group 3 matched, else "<band>-severity " (the separator becomes one space)',
    max_length: CHART_REQUEST_MAX_LENGTH,
  };
}

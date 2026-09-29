// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import {ArtifactStreamParser} from './artifacts';
import type {JsonObject} from './contract';
import {isJsonObject} from './json';

/**
 * A real successful Search tool result chooses the archive route, not words such
 * as "bottle" or "leaking". Keep explicit inspections and multi-capability work
 * on the normal agent route even if they also use Search as an intermediate step.
 */
export function mayCompleteArchiveSearch(message: string): boolean {
  let question = message;
  if (message.startsWith('[Context: ')) {
    const end = message.indexOf(']\n\n');
    if (end < 0) return false;
    try {
      const contexts: unknown = JSON.parse(message.slice(10, end));
      if (!Array.isArray(contexts)) return false;
      if (contexts.some(value => isJsonObject(value) && (
        value.mode === 'live' || value.scope === 'selected_live_session'
        || Array.isArray(value.selected_bottles)
        || typeof value.analysis_kind === 'string'
        || (typeof value.scene_id === 'string' && 'reference_level' in value)
      ))) return false;
    } catch { return false; }
    question = message.slice(end + 3);
  }
  if (/\bcy(?:cle|ce)[\s_-]*\d+\b/i.test(question)) return false;
  if (/\b(?:filling\s+analysis|fill(?:ing)?\s+(?:levels?|heights?|measurements?|statistics|status|summary)|live\s+(?:session|status|filling))\b/i.test(question)) return false;
  if (/\b(?:compare|measure|inspect)\b[\s\S]{0,100}\b(?:bottles?|fill(?:ing)?|cycles?)\b/i.test(question)) return false;
  if (/\b(?:and|then|also|plus)\b[\s\S]{0,100}\b(?:create|start|stop|configure|deploy|summarize|report|measure|inspect|compare|analyze)\b/i.test(question)) return false;
  return true;
}

/** Only explicit new retrieval commands require fresh execution; ordinary chat
 * and questions about previous results retain normal conversation behavior. */
export function requiresFreshArchiveSearch(message: string): boolean {
  if (!mayCompleteArchiveSearch(message)) return false;
  const question = message.startsWith('[Context: ')
    ? message.slice(message.indexOf(']\n\n') + 3).trim() : message.trim();
  const q = question.replace(/^(?:(?:please|can you|could you|would you)\s+)+/i, '');
  if (/^(?:rerun|re-run|retry|repeat|run)\s+(?:(?:that|the|this|last|previous|same)\s+)*(?:search|query)(?:\s+again)?[.!?]?$/i.test(q)) return true;
  if (/^(?:run|perform|execute)\s+(?:(?:a|an|the|new|fresh|archive|visual|video)\s+)*search\b/i.test(q)) return true;
  if (!/^(?:find|show\s+me|search|look\s+for)\b/i.test(q)) return false;
  if (/^show\s+me\s+(?:(?:that|this|those|these|the)\s+)+(?:first|second|third|last|next|previous|top|\d+(?:st|nd|rd|th)?|\d+\s*[-–]\s*\d+(?:\s+seconds?)?)?\s*(?:clip|result|match|video|recording)s?\b/i.test(q)) return false;
  if (/\b(?:previous|earlier|last|prior|old)\s+(?:search|results?|matches?|answers?|clips?)\b/i.test(q)
    || /^(?:show\s+me|find)\s+(?:how|why|what|which|where|whether|out)\b/i.test(q)
    || /\b(?:search history|chat history|previously returned|already found)\b/i.test(q)) return false;
  if (/^(?:show\s+me|find|search(?:\s+for)?)\s+(?:(?:the|all|available|registered|uploaded|online|existing|list\s+of)\s+)*(?:videos?|recordings?|sources?|streams?|cameras?)(?:\s+(?:that\s+are\s+)?(?:available|registered|uploaded|online))?[.!?]?$/i.test(q)) return false;
  if (/^(?:show\s+me|find|search(?:\s+for)?)\s+(?:the\s+)?(?:logs?|configuration|config|code|files?|tools?|skills?|reports?|alerts?|incidents?|dashboards?)\b/i.test(q)) return false;
  return true;
}

export const FRESH_ARCHIVE_SEARCH_INSTRUCTIONS = [
  'This turn requests a NEW archive visual search. Execute an actual vss_cli search run during this turn before answering.',
  'Use the full visual description in the current user request as the search query. Do not shorten it, substitute a previous query, or search only a fragment. If the user explicitly says rerun/retry that search, resolve the referenced full query from the conversation and execute it again.',
  'Search all recordings by default. Add a video-source filter only when the user explicitly selected/named that source, attached source context, or explicitly requested rerunning a previously scoped search. Do not invent a scope merely because vios list contains a recording.',
  'Use --no-merge-adjacent for a new visual Search so verification checks each retrieved window rather than a merged clip containing different events. Preserve an explicit user request for merged results instead of applying that default.',
  'Historical tool output, memory, cached files and previous answers are not a fresh result. Do not read/restate them as the answer to this request.',
  'Do not call unrelated Filling tools or mention old live counts. Return only the result of this turn’s actual Search. A successful empty result is an answer, not a reason to query another capability.',
  'Do not claim a clip is confirmed when its current verification verdict is rejected or unverified. Report a real tool failure rather than substituting old evidence.',
].join('\n');

function cliResult(value: unknown, depth = 0): JsonObject | undefined {
  if (depth > 7) return;
  if (typeof value === 'string' && value.length < 2_000_000) {
    try { return cliResult(JSON.parse(value), depth + 1); } catch { return; }
  }
  if (Array.isArray(value)) {
    for (const item of value.slice(0, 30)) {
      const found = cliResult(item, depth + 1);
      if (found) return found;
    }
    return;
  }
  if (!isJsonObject(value)) return;
  if ('exitCode' in value) {
    if (value.exitCode !== 0 || value.timedOut === true || value.truncated === true
      || (value.signal !== undefined && value.signal !== null)
      || typeof value.command !== 'string' || typeof value.stdout !== 'string'
      || !/^(?:\/[^\s]+\/)?vss\s+search\s+run\s+(?:embed|fusion|attribute|object|tag)(?:\s|$)/u.test(value.command)) return;
    return value;
  }
  for (const key of ['content', 'text', 'result', 'structuredContent']) {
    const found = cliResult(value[key], depth + 1);
    if (found) return found;
  }
}

/** Only a zero-exit CLI SearchOutput paired with its successful job receipt. */
export function completedArchiveSearch(value: unknown): JsonObject | undefined {
  const result = cliResult(value);
  if (!result) return;
  const artifacts = new ArtifactStreamParser().inspectComplete(result.stdout)
    .filter(event => event.type === 'artifact.created' && event.data.kind === 'vss.search.results');
  if (artifacts.length !== 1) return;
  const payload = artifacts[0].data.payload;
  if (!isJsonObject(payload) || !Array.isArray(payload.data)
    || !payload.data.every(isJsonObject) || !Array.isArray(payload.search_messages)) return;
  // CLI completion receipts are emitted as standalone compact JSON lines.
  // An explicit UI artifact envelope alone is not evidence of a current job.
  const matchingReceipt = result.stdout.split(/\r?\n/).some(line => {
    try {
      const receipt: unknown = JSON.parse(line);
      return isJsonObject(receipt) && receipt.event === 'vss_job_completed'
        && receipt.group === 'search' && receipt.status === 'completed'
        && receipt.exit_hint === 0 && typeof receipt.job_id === 'string'
        && receipt.job_id.length > 0 && receipt.job_id === payload.job_id;
    } catch { return false; }
  });
  return matchingReceipt ? payload : undefined;
}

/** Render only facts from this returned result; never infer a spill or live state. */
export function archiveSearchSummary(result: JsonObject): string {
  const count = Array.isArray(result.data) ? result.data.length : 0;
  return count
    ? `Search returned ${count} clip candidate${count === 1 ? '' : 's'}. Review the clips and their verification verdicts in the Search tab.`
    : 'Search returned no clip candidates for this query.';
}

// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT
import {isJsonObject} from './json';
import type {JsonObject} from './contract';

export type FillingScope = {session: string; stream: string; selected: string[]; explicit?: string[]; selectionRequired?: number};
/** Source and selections come from the visible product controls, never an inferred current bottle. */
export function fillingRequestScope(message: string): FillingScope | undefined {
  const end = message.indexOf(']\n\n');
  if (!message.startsWith('[Context: ') || end < 0) return;
  try {
    const contexts: unknown = JSON.parse(message.slice(10, end));
    if (!Array.isArray(contexts)) return;
    const context = contexts.find(value => isJsonObject(value) && value.mode === 'live' && value.scope === 'selected_live_session');
    if (!isJsonObject(context) || typeof context.session_id !== 'string' || typeof context.stream_id !== 'string') return;
    const question = message.slice(end + 3);
    // An explicit cycle label overrides ambient UI selection; archive queries retain their normal rendering.
    if (/\b(?:search|find)\b.*\b(?:recording|recorded|clips?|video)\b/i.test(question)) return;
    const explicit = Array.from(new Set(Array.from(question.matchAll(/\bcy(?:cle|ce)[\s_-]*(\d+)\b/gi), match => `cycle-${Number(match[1])}`)));
    const selected = !explicit.length && Array.isArray(context.selected_bottles)
      ? context.selected_bottles.filter(isJsonObject).map(value => value.track_id).filter((value): value is string => typeof value === 'string') : [];
    const selectionRequired = explicit.length ? undefined
      : /\b(?:these|those) (?:two )?bottles\b/i.test(question) ? 2
      : /\bthis bottle\b/i.test(question) ? 1 : undefined;
    return {session: context.session_id, stream: context.stream_id, selected, explicit, selectionRequired};
  } catch { return; }
}

/** Unbound inspection references require a product selection, not an inferred live track. */
export function fillingResponseText(scope: FillingScope, measured: string | undefined, fallback: string | undefined): string | undefined {
  if (scope.selectionRequired && scope.selected.length !== scope.selectionRequired) {
    return scope.selectionRequired === 1
      ? 'Select exactly one bottle in Filling Analysis, then ask again.'
      : 'Select exactly two bottles in Filling Analysis, then ask again.';
  }
  if ((scope.selectionRequired || scope.explicit?.length) && !measured) {
    return 'No verified inspection result was returned for the requested bottle or bottles. Please try again.';
  }
  return measured || fallback;
}

function decodeOperator(value: unknown, depth = 0): JsonObject | undefined {
  if (depth > 7) return;
  if (typeof value === 'string' && value.length < 1_000_000) {
    try {return decodeOperator(JSON.parse(value), depth + 1);} catch {return;}
  }
  if (Array.isArray(value)) {
    for (const child of value.slice(0, 20)) {const found = decodeOperator(child, depth + 1); if (found) return found;}
  }
  if (!isJsonObject(value)) return;
  if (value.render_policy === 'authoritative_measurement' && value.mode === 'live' && value.view === 'operator') return value;
  if ('exitCode' in value && value.exitCode !== 0) return;
  for (const key of ['stdout', 'content', 'text']) {const found = decodeOperator(value[key], depth + 1); if (found) return found;}
}

/** Render validated measured facts without a second, potentially contradictory model paraphrase. */
export function fillingOperatorText(value: unknown, scope: FillingScope): string | undefined {
  const result = decodeOperator(value);
  if (!result) return;
  if (result.session_id !== scope.session || result.stream_id !== scope.stream
    || typeof result.display_markdown !== 'string' || !result.display_markdown.trim()
    || !Array.isArray(result.matches) || !Array.isArray(result.snapshot_evidences)
    || !['ok', 'unsupported', 'not_found', 'in_progress', 'ambiguous'].includes(String(result.query_status))) {
    throw new Error('Filling operator result does not match the requested source or contract.');
  }
  if (scope.selectionRequired && scope.selected.length !== scope.selectionRequired
    && (result.query_status !== 'unsupported' || result.matches.length > 0
      || (Array.isArray(result.requested_cycle_ids) && result.requested_cycle_ids.length > 0))) {
    throw new Error('Select the requested bottle or pair before inspecting it.');
  }
  if (scope.selected.length) {
    const returned = result.selected_track_ids;
    if (!Array.isArray(returned) || returned.length !== scope.selected.length
      || !scope.selected.every(track => returned.includes(track))) throw new Error('Filling operator result changed the selected bottles.');
    if (result.matches.some(row => !isJsonObject(row) || !scope.selected.includes(String(row.track_id)))) throw new Error('Filling result contains an unselected bottle.');
  }
  if (scope.explicit?.length) {
    const requested = result.requested_cycle_ids;
    if (!Array.isArray(requested) || requested.length !== scope.explicit.length
      || !scope.explicit.every(cycle => requested.includes(cycle))) throw new Error('Filling operator result changed the explicitly requested cycles.');
    // Short labels are numeric aliases; full selected track identities remain exact above.
    const cycleLabel = (row: unknown) => {
      const number = isJsonObject(row) && typeof row.track_id === 'string' ? row.track_id.match(/(?:^|:)cycle-(\d+)$/)?.[1] : undefined;
      return number === undefined ? undefined : `cycle-${Number(number)}`;
    };
    const labels = result.matches.map(cycleLabel);
    if (labels.some(cycle => !cycle || !scope.explicit!.includes(cycle))) throw new Error('Filling result contains a different explicit bottle.');
    if (result.query_status === 'ok' && !scope.explicit.every(cycle => labels.includes(cycle))) throw new Error('Filling result omitted an explicitly requested bottle.');
    if (result.query_status === 'ambiguous' && (!Array.isArray(result.candidates) || result.candidates.some(row => !scope.explicit!.includes(cycleLabel(row) || '')))) throw new Error('Filling ambiguity belongs to another bottle.');
    if (result.query_status === 'in_progress' && !scope.explicit.includes(cycleLabel(result.current) || '')) throw new Error('Filling provisional observation belongs to another bottle.');
  }
  const events = new Set(result.matches.filter(isJsonObject).map(row => row.event_id));
  for (const snapshot of result.snapshot_evidences) {
    if (!isJsonObject(snapshot) || snapshot.session_id !== scope.session || snapshot.stream_id !== scope.stream
      || !events.has(snapshot.event_id) || snapshot.verified !== true || snapshot.verification !== 'exact-decoded-frame') {
      throw new Error('Filling evidence belongs to a different inspection.');
    }
  }
  return result.display_markdown;
}

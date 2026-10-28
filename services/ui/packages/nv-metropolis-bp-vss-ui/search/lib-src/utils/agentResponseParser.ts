// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Extracts Search API–shaped JSON from agent response text and transforms to SearchData[].
 * The agent may return markdown or plain text with an embedded JSON block (e.g. ```json ... ``` or raw { "data": [...] }).
 */
import { extractVssUiArtifacts, replaceVideoUrlBase } from 'common';
import type { SearchData } from '../types';

/** Same shape as the Search API response: { data: Array<...> } */
interface SearchApiShape {
  data?: unknown[];
}

/**
 * Reasoning traces can carry tool inputs, intermediate JSON or malformed code
 * that would otherwise be mistaken for the final Search payload.
 */
const AGENT_THINK_BLOCK = /<agent-think(?![-\w])[^>]*>[\s\S]*?<\/agent-think>/g;

function stripAgentThinkBlocks(text: string): string {
  return text.replace(AGENT_THINK_BLOCK, '');
}

/**
 * Complete `{ ... }` ranges from one left-to-right pass. Restarting a
 * depth scan at every unmatched `{` is quadratic on a long malformed answer
 * and Search runs this on the UI thread.
 *
 * Quotes are only string delimiters inside an object. An unmatched `"` in
 * surrounding prose must not hide a later `{"data":[...]}` payload.
 */
function completeBraceObjects(text: string): { start: number; end: number }[] {
  const objects: { start: number; end: number }[] = [];
  const stack: number[] = [];
  let inString = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text.charAt(i);
    if (stack.length === 0) {
      if (ch === '{') stack.push(i);
      continue;
    }
    if (inString) {
      if (ch === '\\') {
        i += 1;
        continue;
      }
      if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') {
      inString = true;
      continue;
    }
    if (ch === '{') stack.push(i);
    else if (ch === '}') {
      const start = stack.pop();
      if (start !== undefined) objects.push({ start, end: i });
      if (stack.length === 0) inString = false;
    }
  }
  return objects;
}

function asSearchApiShape(value: unknown): SearchApiShape | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const { data } = value as SearchApiShape;
  if (!Array.isArray(data)) return null;
  const allRecords = data.every(
    (item) => item !== null && typeof item === 'object' && !Array.isArray(item),
  );
  return allRecords ? (value as SearchApiShape) : null;
}

function tryParseSearchApiShape(candidate: string): SearchApiShape | null {
  try {
    return asSearchApiShape(JSON.parse(candidate));
  } catch {
    return null;
  }
}

function extractJsonFromCodeBlocks(text: string): SearchApiShape | null {
  let searchFrom = 0;
  while (searchFrom < text.length) {
    const openFence = text.indexOf('```', searchFrom);
    if (openFence === -1) return null;
    let contentStart = openFence + 3;
    if (text.slice(contentStart, contentStart + 4).toLowerCase() === 'json'
      && (contentStart + 4 >= text.length || /\s/.test(text[contentStart + 4]))) {
      contentStart += 4;
    }
    const closeFence = text.indexOf('```', contentStart);
    if (closeFence === -1) return null;
    const parsed = tryParseSearchApiShape(text.slice(contentStart, closeFence).trim());
    if (parsed) return parsed;
    searchFrom = closeFence + 3;
  }
  return null;
}

function extractJsonFromCompleteObjects(text: string): SearchApiShape | null {
  const objects = completeBraceObjects(text);
  objects.sort((a, b) => a.start - b.start);
  let skipUntil = 0;
  for (const { start, end } of objects) {
    if (start < skipUntil) continue;
    try {
      const parsed = asSearchApiShape(JSON.parse(text.slice(start, end + 1)));
      if (parsed) return parsed;
      // Well-formed object that is not the payload: skip it whole so a nested
      // `data` array is not mistaken for the top-level result.
      skipUntil = end + 1;
    } catch {
      // Malformed: inner complete objects may still be the real payload.
      skipUntil = start + 1;
    }
  }
  return null;
}

/**
 * Independent match from each `{`, the way the original scanner recovered a
 * payload after quoted prose such as `"{". Results: {"data":[...]}`.
 * Bounded so a long unmatched-brace prefix cannot go quadratic.
 */
const MAX_INDEPENDENT_BRACE_STARTS = 128;

function findMatchingBrace(text: string, startIndex: number): number {
  let depth = 0;
  let inString = false;
  for (let i = startIndex; i < text.length; i += 1) {
    const ch = text.charAt(i);
    if (inString) {
      if (ch === '\\') i += 1;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') inString = true;
    else if (ch === '{') depth += 1;
    else if (ch === '}') {
      depth -= 1;
      if (depth === 0) return i;
    }
  }
  return -1;
}

function extractJsonFromIndependentBraces(text: string): SearchApiShape | null {
  let brace = text.indexOf('{');
  let starts = 0;
  while (brace !== -1 && starts < MAX_INDEPENDENT_BRACE_STARTS) {
    starts += 1;
    const end = findMatchingBrace(text, brace);
    if (end === -1) {
      brace = text.indexOf('{', brace + 1);
      continue;
    }
    try {
      const parsed = asSearchApiShape(JSON.parse(text.slice(brace, end + 1)));
      if (parsed) return parsed;
      brace = text.indexOf('{', end + 1);
    } catch {
      brace = text.indexOf('{', brace + 1);
    }
  }
  return null;
}

function extractJsonFromText(text: string): SearchApiShape | null {
  return extractJsonFromCompleteObjects(text) ?? extractJsonFromIndependentBraces(text);
}

function transformToSearchData(data: unknown[]): SearchData[] {
  return data.map((item: any) => ({
    video_name: item.video_name || '',
    similarity: Number(item.similarity) || 0,
    screenshot_url: item.screenshot_url || '',
    description: item.description || '',
    start_time: item.start_time || '',
    end_time: item.end_time || '',
    sensor_id: item.sensor_id || '',
    object_ids: Array.isArray(item.object_ids) ? item.object_ids : [],
    critic_result: item.critic_result || undefined,
  }));
}

/**
 * Rebase VIOS thumbnail URLs onto the browser-facing VIOS origin. External
 * harnesses run the VSS CLI from their own network namespace, so an otherwise
 * valid artifact can contain a host-local or container-local VIOS hostname.
 * Playback already applies the same rebasing to the clip URL returned by VIOS.
 */
export function normalizeSearchResultMediaUrls(
  results: SearchData[],
  viosApiUrl?: string,
): SearchData[] {
  if (!viosApiUrl?.includes('/vst/')) return results;

  return results.map((result) => {
    if (!result.screenshot_url.includes('/vst/')) return result;

    const screenshotUrl = replaceVideoUrlBase(result.screenshot_url, viosApiUrl);
    return screenshotUrl === result.screenshot_url
      ? result
      : { ...result, screenshot_url: screenshotUrl };
  });
}

/**
 * Tries to extract a JSON object from text that has the Search API shape { data: [...] }.
 * Tries: (1) a `vss.search.results` artifact, then, outside `<agent-think>` reasoning,
 * (2) every ``` fenced block in order, (3) every top-level { ... } object in order.
 * Returns the transformed SearchData[] or null if no valid JSON found.
 */
export function extractSearchResultsFromAgentResponse(responseText: string): SearchData[] | null {
  if (!responseText || typeof responseText !== 'string') return null;
  const trimmed = responseText.trim();
  const artifact = extractVssUiArtifacts(trimmed).find(
    (candidate) =>
      candidate.kind === 'vss.search.results' && Array.isArray(candidate.payload.data),
  );
  if (artifact) return transformToSearchData(artifact.payload.data as unknown[]);

  const answer = stripAgentThinkBlocks(trimmed);
  const parsed = extractJsonFromCodeBlocks(answer) ?? extractJsonFromText(answer);
  if (!parsed?.data) return null;

  return transformToSearchData(parsed.data);
}

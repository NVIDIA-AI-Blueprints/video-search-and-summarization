// SPDX-License-Identifier: MIT
/**
 * Extracts Search API–shaped JSON from agent response text and transforms to SearchData[].
 * The agent may return markdown or plain text with an embedded JSON block (e.g. ```json ... ``` or raw { "data": [...] }).
 */
import type { SearchData } from '../types';

/** Same shape as the Search API response: { data: Array<...> } */
interface SearchApiShape {
  data?: unknown[];
}

function findMatchingBrace(text: string, startIndex: number): number {
  let depth = 0;
  for (let i = startIndex; i < text.length; i++) {
    if (text[i] === '{') depth++;
    else if (text[i] === '}') {
      depth--;
      if (depth === 0) return i;
    }
  }
  return -1;
}

function extractJsonFromCodeBlock(text: string): SearchApiShape | null {
  const openFence = text.indexOf('```');
  if (openFence === -1) return null;
  let contentStart = openFence + 3;
  if (text.slice(contentStart, contentStart + 4).toLowerCase() === 'json'
    && (contentStart + 4 >= text.length || /\s/.test(text[contentStart + 4]))) {
    contentStart += 4;
  }
  const closeFence = text.indexOf('```', contentStart);
  if (closeFence === -1) return null;
  try {
    return JSON.parse(text.slice(contentStart, closeFence).trim()) as SearchApiShape;
  } catch {
    return null;
  }
}

function extractJsonFromText(text: string): SearchApiShape | null {
  const firstBrace = text.indexOf('{');
  if (firstBrace === -1) return null;
  const end = findMatchingBrace(text, firstBrace);
  if (end === -1) return null;
  try {
    return JSON.parse(text.slice(firstBrace, end + 1)) as SearchApiShape;
  } catch {
    return null;
  }
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
 * Tries to extract a JSON object from text that has the Search API shape { data: [...] }.
 * Tries: (1) ```json ... ``` block, (2) first top-level { ... } in the text.
 * Returns the transformed SearchData[] or null if no valid JSON found.
 */
export function extractSearchResultsFromAgentResponse(responseText: string): SearchData[] | null {
  if (!responseText || typeof responseText !== 'string') return null;
  const trimmed = responseText.trim();

  let parsed = extractJsonFromCodeBlock(trimmed);
  if (!parsed || !Array.isArray(parsed.data)) {
    parsed = extractJsonFromText(trimmed);
  }
  if (!parsed || !Array.isArray(parsed.data)) return null;

  return transformToSearchData(parsed.data);
}

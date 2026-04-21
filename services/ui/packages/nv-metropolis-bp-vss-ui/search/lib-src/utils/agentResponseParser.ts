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

/**
 * Tries to extract a JSON object from text that has the Search API shape { data: [...] }.
 * Tries: (1) ```json ... ``` block, (2) first top-level { ... } in the text.
 * Returns the transformed SearchData[] or null if no valid JSON found.
 */
export function extractSearchResultsFromAgentResponse(responseText: string): SearchData[] | null {
  if (!responseText || typeof responseText !== 'string') return null;
  const trimmed = responseText.trim();
  let parsed: SearchApiShape | null = null;

  const jsonBlockMatch = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (jsonBlockMatch) {
    try {
      parsed = JSON.parse(jsonBlockMatch[1].trim()) as SearchApiShape;
    } catch {
      // ignore
    }
  }
  if (!parsed || !Array.isArray(parsed.data)) {
    const firstBrace = trimmed.indexOf('{');
    if (firstBrace !== -1) {
      let depth = 0;
      let end = -1;
      for (let i = firstBrace; i < trimmed.length; i++) {
        if (trimmed[i] === '{') depth++;
        else if (trimmed[i] === '}') {
          depth--;
          if (depth === 0) {
            end = i;
            break;
          }
        }
      }
      if (end !== -1) {
        try {
          parsed = JSON.parse(trimmed.slice(firstBrace, end + 1)) as SearchApiShape;
        } catch {
          parsed = null;
        }
      }
    }
  }

  if (!parsed || !Array.isArray(parsed.data)) return null;
  const transformed: SearchData[] = (parsed.data || []).map((item: any) => ({
    video_name: item.video_name || '',
    similarity: item.similarity ?? 0,
    screenshot_url: item.screenshot_url || '',
    description: item.description || '',
    start_time: item.start_time || '',
    end_time: item.end_time || '',
    sensor_id: item.sensor_id || '',
    object_ids: Array.isArray(item.object_ids) ? item.object_ids : [],
  }));
  return transformed;
}

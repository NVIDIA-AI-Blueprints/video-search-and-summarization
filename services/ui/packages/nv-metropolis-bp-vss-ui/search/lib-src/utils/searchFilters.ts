// SPDX-License-Identifier: MIT
import type { QueryDataContext, SearchData, SearchParams, StreamInfo } from '../types';
import { formatDateToLocalISO, parseDateAsLocal } from './Formatter';
import { DEFAULT_TOP_K } from '../hooks/useFilter';

export const SEARCH_FILTER_CONTEXT_ID = 'vss-search-filters';

function sourceTypeToStreamType(sourceType?: string): string | null {
  if (sourceType === 'rtsp') return 'sensor_rtsp';
  if (sourceType === 'video_file') return 'sensor_file';
  return null;
}

/**
 * Apply Search-tab source/filter controls to Chat-derived result cards so the
 * visible grid matches the filters the user set.
 */
export function applySearchResultFilters(
  results: SearchData[],
  params: SearchParams,
  streams: StreamInfo[] = [],
): SearchData[] {
  const { startDate, endDate, videoSources, similarity, topK, sourceType } = params;
  const minSimilarity = Number(similarity);
  const hasMinSimilarity = Number.isFinite(minSimilarity) && minSimilarity !== 0;
  const sourceNames = new Set((videoSources || []).filter(Boolean));
  const streamById = new Map(streams.map((s) => [s.sensorId, s]));
  const streamByName = new Map(streams.map((s) => [s.name, s]));
  const requiredStreamType = sourceTypeToStreamType(sourceType);

  const filtered = results.filter((item) => {
    if (hasMinSimilarity && Number(item.similarity) < minSimilarity) return false;

    if (sourceNames.size > 0) {
      const stream = streamById.get(item.sensor_id);
      const matchesName =
        sourceNames.has(item.video_name) ||
        sourceNames.has(item.sensor_id) ||
        (stream != null && sourceNames.has(stream.name));
      if (!matchesName) return false;
    }

    if (requiredStreamType && streams.length > 0) {
      const stream = streamById.get(item.sensor_id) || streamByName.get(item.video_name);
      if (stream?.type && stream.type !== requiredStreamType) return false;
    }

    if (startDate) {
      const start = parseDateAsLocal(item.start_time);
      if (start && start < startDate) return false;
    }
    if (endDate) {
      const end = parseDateAsLocal(item.end_time) || parseDateAsLocal(item.start_time);
      if (end && end > endDate) return false;
    }

    return true;
  });

  const limit = Number(topK);
  if (Number.isFinite(limit) && limit >= 1) {
    return filtered.slice(0, limit);
  }
  return filtered;
}

export function buildSearchFilterChatContext(params: SearchParams): QueryDataContext {
  const data: Record<string, unknown> = {
    source_type: params.sourceType || 'video_file',
    top_k: params.topK ?? DEFAULT_TOP_K,
  };
  if (params.videoSources && params.videoSources.length > 0) {
    data.video_sources = params.videoSources;
  }
  const timestampStart = formatDateToLocalISO(params.startDate ?? null);
  const timestampEnd = formatDateToLocalISO(params.endDate ?? null);
  if (timestampStart) data.timestamp_start = timestampStart;
  if (timestampEnd) data.timestamp_end = timestampEnd;
  const minSimilarity = Number(params.similarity);
  if (Number.isFinite(minSimilarity) && minSimilarity !== 0) {
    data.min_cosine_similarity = Number(minSimilarity.toFixed(2));
  }
  return {
    id: SEARCH_FILTER_CONTEXT_ID,
    label: 'Search filters',
    contextType: 'search/filters',
    data,
  };
}

export function prefixMessageWithSearchFilters(message: string, params: SearchParams): string {
  const ctx = buildSearchFilterChatContext(params);
  return `[Context: ${JSON.stringify([ctx.data])}]\n\n${message}`;
}

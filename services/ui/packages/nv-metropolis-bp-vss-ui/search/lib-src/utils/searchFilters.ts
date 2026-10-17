// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import type { SearchData, SearchParams, StreamInfo } from '../types';

/**
 * Apply Search-tab filter controls to Chat-derived result cards so the
 * visible grid matches the filters the user set.
 */
export function applySearchResultFilters(
  results: SearchData[],
  params: SearchParams,
  streams: StreamInfo[] = [],
): SearchData[] {
  const { videoSources, similarity, topK } = params;
  const minSimilarity = Number(similarity);
  const hasMinSimilarity = Number.isFinite(minSimilarity) && minSimilarity !== 0;
  const sourceNames = new Set((videoSources || []).filter(Boolean));
  const streamById = new Map(streams.map((s) => [s.sensorId, s]));

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

    return true;
  });

  const limit = Number(topK);
  if (Number.isFinite(limit) && limit >= 1) {
    return filtered.slice(0, limit);
  }
  return filtered;
}

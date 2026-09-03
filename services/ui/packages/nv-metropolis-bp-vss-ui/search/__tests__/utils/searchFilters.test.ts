// SPDX-License-Identifier: MIT
import {
  applySearchResultFilters,
  buildSearchFilterChatContext,
  prefixMessageWithSearchFilters,
  SEARCH_FILTER_CONTEXT_ID,
} from '../../lib-src/utils/searchFilters';
import type { SearchData, StreamInfo } from '../../lib-src/types';

const clip = (overrides: Partial<SearchData> = {}): SearchData => ({
  video_name: 'warehouse.mp4',
  description: 'scene',
  start_time: '2024-01-15T09:00:00',
  end_time: '2024-01-15T09:05:00',
  sensor_id: 'sensor-1',
  similarity: 0.8,
  screenshot_url: '',
  object_ids: [],
  ...overrides,
});

const streams: StreamInfo[] = [
  { sensorId: 'sensor-1', name: 'warehouse.mp4', type: 'sensor_file' },
  { sensorId: 'sensor-2', name: 'gate.mp4', type: 'sensor_rtsp' },
];

describe('applySearchResultFilters', () => {
  it('drops results below min similarity', () => {
    const results = [clip({ similarity: 0.2 }), clip({ video_name: 'hi.mp4', similarity: 0.9 })];
    const filtered = applySearchResultFilters(results, { similarity: 0.5 }, streams);
    expect(filtered).toHaveLength(1);
    expect(filtered[0].video_name).toBe('hi.mp4');
  });

  it('keeps only selected video sources', () => {
    const results = [
      clip({ video_name: 'warehouse.mp4', sensor_id: 'sensor-1' }),
      clip({ video_name: 'gate.mp4', sensor_id: 'sensor-2' }),
    ];
    const filtered = applySearchResultFilters(results, { videoSources: ['warehouse.mp4'] }, streams);
    expect(filtered.map((r) => r.video_name)).toEqual(['warehouse.mp4']);
  });

  it('filters by source type via stream metadata', () => {
    const results = [
      clip({ video_name: 'warehouse.mp4', sensor_id: 'sensor-1' }),
      clip({ video_name: 'gate.mp4', sensor_id: 'sensor-2' }),
    ];
    const filtered = applySearchResultFilters(results, { sourceType: 'rtsp' }, streams);
    expect(filtered.map((r) => r.video_name)).toEqual(['gate.mp4']);
  });

  it('slices to topK', () => {
    const results = [
      clip({ video_name: 'a.mp4', similarity: 0.9 }),
      clip({ video_name: 'b.mp4', similarity: 0.8 }),
      clip({ video_name: 'c.mp4', similarity: 0.7 }),
    ];
    const filtered = applySearchResultFilters(results, { topK: 2 }, streams);
    expect(filtered).toHaveLength(2);
  });

  it('filters by start/end window', () => {
    const results = [
      clip({ video_name: 'early.mp4', start_time: '2024-01-01T00:00:00', end_time: '2024-01-01T00:05:00' }),
      clip({ video_name: 'late.mp4', start_time: '2024-06-01T00:00:00', end_time: '2024-06-01T00:05:00' }),
    ];
    const filtered = applySearchResultFilters(
      results,
      { startDate: new Date(2024, 5, 1, 0, 0, 0), endDate: new Date(2024, 5, 1, 23, 59, 59) },
      streams,
    );
    expect(filtered.map((r) => r.video_name)).toEqual(['late.mp4']);
  });

  it('keeps segments overlapping the window at either boundary', () => {
    const results = [
      // Starts before the window, ends inside it.
      clip({ video_name: 'straddles-start.mp4', start_time: '2024-06-01T08:50:00', end_time: '2024-06-01T09:10:00' }),
      // Starts inside the window, ends after it.
      clip({ video_name: 'straddles-end.mp4', start_time: '2024-06-01T09:50:00', end_time: '2024-06-01T10:10:00' }),
      // Spans the whole window.
      clip({ video_name: 'spans.mp4', start_time: '2024-06-01T08:00:00', end_time: '2024-06-01T11:00:00' }),
      clip({ video_name: 'before.mp4', start_time: '2024-06-01T07:00:00', end_time: '2024-06-01T07:30:00' }),
      clip({ video_name: 'after.mp4', start_time: '2024-06-01T12:00:00', end_time: '2024-06-01T12:30:00' }),
    ];

    const filtered = applySearchResultFilters(
      results,
      { startDate: new Date(2024, 5, 1, 9, 0, 0), endDate: new Date(2024, 5, 1, 10, 0, 0) },
      streams,
    );

    expect(filtered.map((r) => r.video_name)).toEqual([
      'straddles-start.mp4',
      'straddles-end.mp4',
      'spans.mp4',
    ]);
  });
});

describe('buildSearchFilterChatContext', () => {
  it('serializes active filters for Chat [Context] payload', () => {
    const ctx = buildSearchFilterChatContext({
      sourceType: 'rtsp',
      topK: 5,
      videoSources: ['cam-1'],
      similarity: 0.4,
      startDate: new Date(2024, 0, 15, 9, 0, 0),
    });
    expect(ctx.id).toBe(SEARCH_FILTER_CONTEXT_ID);
    expect(ctx.data).toEqual({
      source_type: 'rtsp',
      top_k: 5,
      video_sources: ['cam-1'],
      timestamp_start: '2024-01-15T09:00:00',
      min_cosine_similarity: 0.4,
    });
  });
});

describe('prefixMessageWithSearchFilters', () => {
  it('prefixes the user message with Context JSON', () => {
    const prefixed = prefixMessageWithSearchFilters('find people', { sourceType: 'video_file', topK: 10 });
    expect(prefixed.startsWith('[Context: ')).toBe(true);
    expect(prefixed).toContain('find people');
    expect(prefixed).toContain('"source_type":"video_file"');
  });
});

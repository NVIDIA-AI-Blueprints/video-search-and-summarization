// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { applySearchResultFilters } from '../../lib-src/utils/searchFilters';
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

  it('slices to topK', () => {
    const results = [
      clip({ video_name: 'a.mp4', similarity: 0.9 }),
      clip({ video_name: 'b.mp4', similarity: 0.8 }),
      clip({ video_name: 'c.mp4', similarity: 0.7 }),
    ];
    const filtered = applySearchResultFilters(results, { topK: 2 }, streams);
    expect(filtered).toHaveLength(2);
  });
});

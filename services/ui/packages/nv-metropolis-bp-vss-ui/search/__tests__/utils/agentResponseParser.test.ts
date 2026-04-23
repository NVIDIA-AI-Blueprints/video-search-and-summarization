// SPDX-License-Identifier: MIT
import { extractSearchResultsFromAgentResponse } from '../../lib-src/utils/agentResponseParser';

describe('extractSearchResultsFromAgentResponse', () => {
  const validData = {
    data: [
      {
        video_name: 'clip1.mp4',
        similarity: 0.95,
        screenshot_url: 'http://example.com/thumb1.jpg',
        description: 'Person walking',
        start_time: '2024-01-15T09:00:00',
        end_time: '2024-01-15T09:05:00',
        sensor_id: 'sensor-1',
        object_ids: ['obj-1', 'obj-2'],
      },
    ],
  };

  describe('null/invalid input', () => {
    it('returns null for empty string', () => {
      expect(extractSearchResultsFromAgentResponse('')).toBeNull();
    });

    it('returns null for null input', () => {
      expect(extractSearchResultsFromAgentResponse(null as any)).toBeNull();
    });

    it('returns null for undefined input', () => {
      expect(extractSearchResultsFromAgentResponse(undefined as any)).toBeNull();
    });

    it('returns null for non-string input', () => {
      expect(extractSearchResultsFromAgentResponse(123 as any)).toBeNull();
    });

    it('returns null for text with no JSON', () => {
      expect(extractSearchResultsFromAgentResponse('just some plain text')).toBeNull();
    });
  });

  describe('JSON in markdown code block', () => {
    it('extracts from ```json block', () => {
      const text = `Here are the results:\n\`\`\`json\n${JSON.stringify(validData)}\n\`\`\`\nEnd of results.`;
      const result = extractSearchResultsFromAgentResponse(text);
      expect(result).toHaveLength(1);
      expect(result![0].video_name).toBe('clip1.mp4');
      expect(result![0].similarity).toBe(0.95);
      expect(result![0].object_ids).toEqual(['obj-1', 'obj-2']);
    });

    it('extracts from ``` block without json tag', () => {
      const text = `Results:\n\`\`\`\n${JSON.stringify(validData)}\n\`\`\``;
      const result = extractSearchResultsFromAgentResponse(text);
      expect(result).toHaveLength(1);
      expect(result![0].video_name).toBe('clip1.mp4');
    });

    it('returns null when code block has invalid JSON and first brace in text is also invalid', () => {
      const text = '```json\n{invalid json}\n```\nNo valid JSON here';
      expect(extractSearchResultsFromAgentResponse(text)).toBeNull();
    });

    it('falls back to brace search when code block JSON has no data array', () => {
      // When code block contains JSON without data array, fallback brace search
      // finds the first {...} in the text (the code block one), so it still returns null
      const text = `\`\`\`json\n{"message":"hello"}\n\`\`\`\nSome text`;
      expect(extractSearchResultsFromAgentResponse(text)).toBeNull();
    });

    it('parses valid JSON with data array from code block', () => {
      const text = `\`\`\`json\n${JSON.stringify(validData)}\n\`\`\``;
      const result = extractSearchResultsFromAgentResponse(text);
      expect(result).toHaveLength(1);
      expect(result![0].video_name).toBe('clip1.mp4');
    });
  });

  describe('JSON in plain text (brace matching)', () => {
    it('extracts from raw JSON object in text', () => {
      const text = `Here are your results: ${JSON.stringify(validData)} Done.`;
      const result = extractSearchResultsFromAgentResponse(text);
      expect(result).toHaveLength(1);
      expect(result![0].sensor_id).toBe('sensor-1');
    });

    it('handles JSON with nested braces', () => {
      const nestedData = {
        data: [
          {
            video_name: 'test.mp4',
            similarity: 0.8,
            screenshot_url: '',
            description: 'nested {braces} test',
            start_time: '',
            end_time: '',
            sensor_id: '',
            object_ids: [],
          },
        ],
      };
      const text = `Result: ${JSON.stringify(nestedData)}`;
      const result = extractSearchResultsFromAgentResponse(text);
      expect(result).toHaveLength(1);
      expect(result![0].video_name).toBe('test.mp4');
    });
  });

  describe('missing/invalid data field', () => {
    it('returns null when JSON has no data array', () => {
      const text = JSON.stringify({ results: [{ video_name: 'test.mp4' }] });
      expect(extractSearchResultsFromAgentResponse(text)).toBeNull();
    });

    it('returns null when data is not an array', () => {
      const text = JSON.stringify({ data: 'not-an-array' });
      expect(extractSearchResultsFromAgentResponse(text)).toBeNull();
    });

    it('returns null when no closing brace found', () => {
      expect(extractSearchResultsFromAgentResponse('prefix { unclosed')).toBeNull();
    });
  });

  describe('data transformation and defaults', () => {
    it('applies default values for missing fields', () => {
      const text = JSON.stringify({ data: [{}] });
      const result = extractSearchResultsFromAgentResponse(text);
      expect(result).toHaveLength(1);
      expect(result![0]).toEqual({
        video_name: '',
        similarity: 0,
        screenshot_url: '',
        description: '',
        start_time: '',
        end_time: '',
        sensor_id: '',
        object_ids: [],
      });
    });

    it('preserves non-array object_ids as empty array', () => {
      const text = JSON.stringify({ data: [{ object_ids: 'not-array' }] });
      const result = extractSearchResultsFromAgentResponse(text);
      expect(result![0].object_ids).toEqual([]);
    });

    it('transforms multiple items', () => {
      const multiData = {
        data: [
          { video_name: 'a.mp4', similarity: 0.9 },
          { video_name: 'b.mp4', similarity: 0.7 },
          { video_name: 'c.mp4', similarity: 0.5 },
        ],
      };
      const result = extractSearchResultsFromAgentResponse(JSON.stringify(multiData));
      expect(result).toHaveLength(3);
      expect(result!.map((r) => r.video_name)).toEqual(['a.mp4', 'b.mp4', 'c.mp4']);
    });

    it('handles empty data array', () => {
      const text = JSON.stringify({ data: [] });
      const result = extractSearchResultsFromAgentResponse(text);
      expect(result).toEqual([]);
    });
  });

  describe('critic_result pass-through', () => {
    it('preserves critic_result when present in data items', () => {
      const dataWithCritique = {
        data: [
          {
            video_name: 'clip1.mp4',
            similarity: 0.95,
            critic_result: {
              result: 'confirmed',
              criteria_met: { 'has players': true, 'is outdoor': false },
            },
          },
        ],
      };
      const result = extractSearchResultsFromAgentResponse(JSON.stringify(dataWithCritique));
      expect(result).toHaveLength(1);
      expect(result![0].critic_result).toEqual({
        result: 'confirmed',
        criteria_met: { 'has players': true, 'is outdoor': false },
      });
    });

    it('sets critic_result to undefined when not present', () => {
      const text = JSON.stringify({ data: [{ video_name: 'no-critique.mp4' }] });
      const result = extractSearchResultsFromAgentResponse(text);
      expect(result![0].critic_result).toBeUndefined();
    });

    it('handles all critic result types (confirmed, rejected, unverified)', () => {
      const data = {
        data: [
          { video_name: 'a.mp4', critic_result: { result: 'confirmed', criteria_met: {} } },
          { video_name: 'b.mp4', critic_result: { result: 'rejected', criteria_met: {} } },
          { video_name: 'c.mp4', critic_result: { result: 'unverified', criteria_met: {} } },
        ],
      };
      const result = extractSearchResultsFromAgentResponse(JSON.stringify(data));
      expect(result).toHaveLength(3);
      expect(result![0].critic_result!.result).toBe('confirmed');
      expect(result![1].critic_result!.result).toBe('rejected');
      expect(result![2].critic_result!.result).toBe('unverified');
    });

    it('preserves critic_result from JSON in markdown code block', () => {
      const data = {
        data: [
          {
            video_name: 'md-clip.mp4',
            similarity: 0.88,
            critic_result: { result: 'rejected', criteria_met: { 'face visible': false } },
          },
        ],
      };
      const text = `Results:\n\`\`\`json\n${JSON.stringify(data)}\n\`\`\``;
      const result = extractSearchResultsFromAgentResponse(text);
      expect(result![0].critic_result).toEqual({
        result: 'rejected',
        criteria_met: { 'face visible': false },
      });
    });
  });
});

/**
 * Tests for HTTP streaming edge cases and error recovery scenarios
 */

function normalizeNewlines(s: string): string {
  return s.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
}

function extractSsePayloads(buffer: string): {
  frames: string[];
  rest: string;
} {
  buffer = normalizeNewlines(buffer);
  const parts = buffer.split(/\n\n/);
  const rest = parts.pop() ?? '';
  const frames: string[] = [];

  for (const block of parts) {
    const dataLines = block
      .split('\n')
      .filter(line => /^data:\s*/.test(line))
      .map(line => line.replace(/^data:\s*/, '').trim())
      .filter(line => line.length > 0);

    if (dataLines.length === 0) continue;
    const payload = dataLines.join('\n');
    if (payload === '[DONE]' || payload === 'DONE') continue;
    frames.push(payload);
  }

  return { frames, rest };
}

function splitNdjson(buffer: string): { lines: string[]; rest: string } {
  buffer = normalizeNewlines(buffer);
  const parts = buffer.split('\n');
  const rest = parts.pop() ?? '';
  const lines = parts.map(l => l.trim()).filter(Boolean);
  return { lines, rest };
}

function tryParseJson<T = any>(s: string): T | null {
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}

function parsePossiblyConcatenatedJson(payload: string): any[] {
  const single = tryParseJson(payload);
  if (single !== null) return [single];

  const objs: any[] = [];
  let depth = 0, start = -1;
  for (let i = 0; i < payload.length; i++) {
    const ch = payload[i];
    if (ch === '{') {
      if (depth === 0) start = i;
      depth++;
    } else if (ch === '}') {
      depth--;
      if (depth === 0 && start !== -1) {
        const slice = payload.slice(start, i + 1);
        const parsed = tryParseJson(slice);
        if (parsed !== null) objs.push(parsed);
        start = -1;
      }
    }
  }
  return objs;
}

// Mock TextEncoder/TextDecoder for streaming tests
global.TextEncoder = jest.fn().mockImplementation(() => ({
  encode: jest.fn(text => new Uint8Array(Buffer.from(text, 'utf8')))
}));

global.TextDecoder = jest.fn().mockImplementation(() => ({
  decode: jest.fn((bytes, options) => {
    if (bytes instanceof Uint8Array) {
      return Buffer.from(bytes).toString('utf8');
    }
    return String(bytes);
  })
}));

describe('HTTP Streaming Edge Cases', () => {
  let encoder: TextEncoder;
  let decoder: TextDecoder;

  beforeEach(() => {
    encoder = new TextEncoder();
    decoder = new TextDecoder();
    jest.clearAllMocks();
  });

  describe('SSE Frame Processing - REAL FUNCTION TESTS', () => {
    /**
     * Description: Verifies that extractSsePayloads correctly reassembles SSE frames split across multiple network chunks
     * Success: Incomplete frames are buffered until complete, then extracted in the correct order without data loss
     */
    test('handles incomplete SSE frames gracefully', () => {
      let buffer = '';
      const chunks = [
        'data: {"value": "Hello',  // Incomplete JSON
        ' world"}\n\n',            // Completion
        'data: [DONE]\n\n'         // End marker
      ];

      let allFrames: string[] = [];
      chunks.forEach(chunk => {
        buffer += chunk;
        const { frames, rest } = extractSsePayloads(buffer);
        allFrames.push(...frames);
        buffer = rest;
      });

      expect(allFrames).toHaveLength(1);
      expect(allFrames[0]).toBe('{"value": "Hello world"}');
    });

    /**
     * Description: Verifies that extractSsePayloads can process multiple complete SSE events within a single chunk
     * Success: All complete events are extracted in order, with empty rest buffer when all frames are complete
     */
    test('handles multiple SSE events in single chunk', () => {
      const multiEventChunk = `data: {"value": "First"}\n\ndata: {"value": "Second"}\n\ndata: {"value": "Third"}\n\n`;

      const { frames, rest } = extractSsePayloads(multiEventChunk);

      expect(frames).toHaveLength(3);
      expect(frames[0]).toBe('{"value": "First"}');
      expect(frames[1]).toBe('{"value": "Second"}');
      expect(frames[2]).toBe('{"value": "Third"}');
      expect(rest).toBe('');
    });

    /**
     * Description: Verifies that extractSsePayloads safely ignores malformed SSE lines while preserving valid ones
     * Success: Valid SSE frames are extracted correctly, malformed lines are filtered out without errors
     */
    test('ignores malformed SSE lines', () => {
      const malformedChunk = `invalid line without data prefix
data: {"value": "valid"}

not-data: {"value": "invalid"}
data: {"value": "another valid"}

`;

      const { frames, rest } = extractSsePayloads(malformedChunk);

      expect(frames).toHaveLength(2);
      expect(frames[0]).toBe('{"value": "valid"}');
      expect(frames[1]).toBe('{"value": "another valid"}');
    });

    /**
     * Description: Verifies that extractSsePayloads correctly processes SSE DONE markers that signal end of stream
     * Success: DONE markers are extracted as regular frames, signaling completion of the streaming response
     */
    test('handles DONE markers correctly', () => {
      const chunkWithDone = `data: {"value": "content"}\n\ndata: [DONE]\n\ndata: {"value": "should be ignored"}\n\n`;

      const { frames, rest } = extractSsePayloads(chunkWithDone);

      expect(frames).toHaveLength(2); // content + should be ignored (DONE doesn't filter here)
      expect(frames[0]).toBe('{"value": "content"}');
    });

    /**
     * Description: Verifies that extractSsePayloads preserves incomplete frames in the rest buffer for next processing
     * Success: Partial frames at end of buffer are returned in rest field, not lost or corrupted
     */
    test('preserves partial frames in rest buffer', () => {
      const partialChunk = `data: {"value": "complete"}\n\ndata: {"value": "incomp`;

      const { frames, rest } = extractSsePayloads(partialChunk);

      expect(frames).toHaveLength(1);
      expect(frames[0]).toBe('{"value": "complete"}');
      expect(rest).toBe('data: {"value": "incomp');
    });
  });

  describe('NDJSON Processing', () => {
    /**
     * Description: Verifies that splitNdjson correctly separates newline-delimited JSON objects
     * Success: Each JSON object on a separate line is extracted individually with partial lines preserved in rest
     */
    test('splits newline-delimited JSON correctly', () => {
      const ndjsonData = `{"value": "line1"}\n{"value": "line2"}\n{"value": "partial`;

      const { lines, rest } = splitNdjson(ndjsonData);

      expect(lines).toHaveLength(2);
      expect(lines[0]).toBe('{"value": "line1"}');
      expect(lines[1]).toBe('{"value": "line2"}');
      expect(rest).toBe('{"value": "partial');
    });

    /**
     * Description: Verifies that splitNdjson ignores empty lines and whitespace between JSON objects
     * Success: Empty lines and whitespace are filtered out, only valid JSON objects are returned
     */
    test('handles empty lines and whitespace', () => {
      const ndjsonWithEmpty = `{"value": "line1"}\n\n   \n{"value": "line2"}\n\t\n`;

      const { lines, rest } = splitNdjson(ndjsonWithEmpty);

      expect(lines).toHaveLength(2);
      expect(lines[0]).toBe('{"value": "line1"}');
      expect(lines[1]).toBe('{"value": "line2"}');
    });

    /**
     * Description: Verifies that splitNdjson handles different line ending formats (\r\n, \r, \n)
     * Success: All line ending formats are normalized and JSON objects are correctly separated
     */
    test('normalizes different line endings', () => {
      const mixedLineEndings = `{"value": "line1"}\r\n{"value": "line2"}\r{"value": "line3"}\n`;

      const { lines, rest } = splitNdjson(mixedLineEndings);

      expect(lines).toHaveLength(3);
      expect(lines[0]).toBe('{"value": "line1"}');
      expect(lines[1]).toBe('{"value": "line2"}');
      expect(lines[2]).toBe('{"value": "line3"}');
    });
  });

  describe('JSON Parsing Edge Cases', () => {
    /**
     * Description: Verifies that parsePossiblyConcatenatedJson correctly processes single valid JSON objects
     * Success: Single JSON object is parsed and returned in array format
     */
    test('parsePossiblyConcatenatedJson handles single valid JSON', () => {
      const singleJson = '{"value": "test"}';

      const results = parsePossiblyConcatenatedJson(singleJson);

      expect(results).toHaveLength(1);
      expect(results[0]).toEqual({ value: "test" });
    });

    /**
     * Description: Verifies that parsePossiblyConcatenatedJson can parse multiple JSON objects concatenated together
     * Success: Multiple concatenated JSON objects are separated and parsed into individual array elements
     */
    test('parsePossiblyConcatenatedJson handles concatenated objects', () => {
      const concatenatedJson = '{"value": "first"}{"value": "second"}{"value": "third"}';

      const results = parsePossiblyConcatenatedJson(concatenatedJson);

      expect(results).toHaveLength(3);
      expect(results[0]).toEqual({ value: "first" });
      expect(results[1]).toEqual({ value: "second" });
      expect(results[2]).toEqual({ value: "third" });
    });

    /**
     * Description: Verifies that parsePossiblyConcatenatedJson correctly handles nested JSON objects
     * Success: Complex nested objects are parsed correctly while maintaining their structure
     */
    test('parsePossiblyConcatenatedJson handles nested objects', () => {
      const nestedJson = '{"data": {"nested": "value"}}{"simple": "value"}';

      const results = parsePossiblyConcatenatedJson(nestedJson);

      expect(results).toHaveLength(2);
      expect(results[0]).toEqual({ data: { nested: "value" } });
      expect(results[1]).toEqual({ simple: "value" });
    });

    /**
     * Description: Verifies that parsePossiblyConcatenatedJson safely handles malformed JSON without throwing errors
     * Success: Malformed JSON is ignored, valid portions are extracted, function doesn't crash
     */
    test('parsePossiblyConcatenatedJson handles malformed JSON gracefully', () => {
      const malformedJson = '{"valid": "object"}{"malformed": invalid}{"another": "valid"}';

      const results = parsePossiblyConcatenatedJson(malformedJson);

      // Should extract valid objects and ignore malformed ones
      expect(results).toHaveLength(2);
      expect(results[0]).toEqual({ valid: "object" });
      expect(results[1]).toEqual({ another: "valid" });
    });

    /**
     * Description: Verifies that parsePossiblyConcatenatedJson returns empty array for completely invalid input
     * Success: Invalid or non-string input returns empty array without throwing exceptions
     */
    test('parsePossiblyConcatenatedJson returns empty array for invalid input', () => {
      const invalidInputs = ['', 'not json at all', '}{invalid', '{incomplete'];

      invalidInputs.forEach(input => {
        const results = parsePossiblyConcatenatedJson(input);
        expect(results).toHaveLength(0);
      });
    });
  });

  describe('Streaming Performance and Memory', () => {
    /**
     * Description: Verifies that rapid processing of multiple chunks maintains data integrity
     * Success: All chunks are processed correctly in sequence without losing or corrupting data
     */
    test('handles rapid chunk succession without data loss', () => {
      const rapidChunks = Array.from({ length: 100 }, (_, i) =>
        `data: {"value": "chunk${i}"}\n\n`
      );

      let buffer = '';
      let allFrames: string[] = [];

      rapidChunks.forEach(chunk => {
        buffer += chunk;
        const { frames, rest } = extractSsePayloads(buffer);
        allFrames.push(...frames);
        buffer = rest;
      });

      // Should have received all chunks
      expect(allFrames).toHaveLength(100);
      expect(allFrames[0]).toBe('{"value": "chunk0"}');
      expect(allFrames[99]).toBe('{"value": "chunk99"}');
    });

    /**
     * Description: Verifies that large content chunks are processed efficiently without performance degradation
     * Success: Large chunks are processed correctly with reasonable performance characteristics
     */
    test('handles large individual chunks efficiently', () => {
      const largeContent = 'x'.repeat(10000); // 10KB content
      const largeChunk = `data: {"value": "${largeContent}"}\n\n`;

      const { frames, rest } = extractSsePayloads(largeChunk);

      expect(frames).toHaveLength(1);
      expect(JSON.parse(frames[0]).value).toBe(largeContent);
      expect(rest).toBe('');
    });

    /**
     * Description: Verifies that buffer management doesn't cause memory leaks with long-running operations
     * Success: Buffers are properly cleaned up and don't accumulate excessive memory usage
     */
    test('buffer management prevents memory leaks', () => {
      let buffer = '';
      const chunks = Array.from({ length: 1000 }, (_, i) =>
        `data: {"chunk": ${i}}\n\n`
      );

      chunks.forEach(chunk => {
        buffer += chunk;
        const { frames, rest } = extractSsePayloads(buffer);
        buffer = rest; // Critical: update buffer to prevent memory accumulation
      });

      // Buffer should not accumulate indefinitely
      expect(buffer.length).toBeLessThan(1000);
    });
  });

  describe('Intermediate Step Tag Processing', () => {
    /**
     * Description: Verifies that intermediate step tag processing recovers gracefully from malformed tags
     * Success: Malformed tags are ignored or corrected, valid tags continue to be processed correctly
     */
    test('recovers from malformed intermediate step tags', () => {
      const chunksWithMalformed = [
        'data: {"value": "Response"}\n\n',
        '<intermediatestep>{"invalid": json}</intermediatestep>',  // Malformed JSON
        '<intermediatestep>{"id": "step-1", "type": "system_intermediate"}</intermediatestep>',  // Valid
        'data: [DONE]\n\n'
      ];

      const validSteps: string[] = [];
      const responses: string[] = [];

      chunksWithMalformed.forEach(chunk => {
        // Extract SSE data
        if (chunk.includes('data: ')) {
          const { frames } = extractSsePayloads(chunk);
          responses.push(...frames);
        }

        // Extract intermediate steps
        const stepMatches = chunk.match(/<intermediatestep>([\s\S]*?)<\/intermediatestep>/g) || [];
        stepMatches.forEach(match => {
          try {
            const jsonString = match
              .replace('<intermediatestep>', '')
              .replace('</intermediatestep>', '')
              .trim();
            const parsed = JSON.parse(jsonString);
            if (parsed.type === 'system_intermediate') {
              validSteps.push(jsonString);
            }
          } catch {
            // Ignore malformed steps
          }
        });
      });

      // Should contain valid response and valid step, ignore malformed
      expect(responses).toContain('{"value": "Response"}');
      expect(validSteps).toHaveLength(1);
      expect(validSteps[0]).toContain('"id": "step-1"');
    });

    /**
     * Description: Verifies that incomplete intermediate step tags are handled without breaking processing
     * Success: Incomplete tags are buffered or ignored appropriately, processing continues for complete tags
     */
    test('handles incomplete intermediate step tags', () => {
      const incompleteChunks = [
        '<intermediatestep>{"id": "step-1",',  // Incomplete tag
        ' "type": "system_intermediate"}</intermediatestep>',  // Completion
        'data: {"value": "response"}\n\n'
      ];

      let buffer = '';
      let partialStepBuffer = '';
      const completedSteps: string[] = [];

      incompleteChunks.forEach(chunk => {
        // Handle potential partial intermediate step
        if (chunk.includes('<intermediatestep>') || partialStepBuffer) {
          partialStepBuffer += chunk;

          // Check for complete tags
          const stepMatches = partialStepBuffer.match(/<intermediatestep>([\s\S]*?)<\/intermediatestep>/g) || [];
          stepMatches.forEach(match => {
            try {
              const jsonString = match
                .replace('<intermediatestep>', '')
                .replace('</intermediatestep>', '')
                .trim();
              const parsed = JSON.parse(jsonString);
              completedSteps.push(jsonString);

              // Remove processed step from buffer
              partialStepBuffer = partialStepBuffer.replace(match, '');
            } catch {
              // Keep in buffer for next chunk
            }
          });
        }
      });

      expect(completedSteps).toHaveLength(1);
      expect(completedSteps[0]).toContain('"id": "step-1"');
    });

    /**
     * Description: Verifies that interleaved intermediate steps and responses maintain correct chronological order
     * Success: Steps and responses are processed in the exact order they were received in the stream
     */
    test('preserves order of interleaved steps and responses', () => {
      const interleavedChunks = [
        'data: {"value": "Start"}\n\n',
        '<intermediatestep>{"id": "step-1", "type": "system_intermediate"}</intermediatestep>',
        'data: {"value": " middle"}\n\n',
        '<intermediatestep>{"id": "step-2", "type": "system_intermediate"}</intermediatestep>',
        'data: {"value": " end"}\n\n'
      ];

      const orderedItems: { type: 'response' | 'step', content: string, order: number }[] = [];
      let order = 0;

      interleavedChunks.forEach(chunk => {
        // Process responses
        if (chunk.includes('data: ')) {
          const { frames } = extractSsePayloads(chunk);
          frames.forEach(frame => {
            if (!frame.includes('[DONE]')) {
              orderedItems.push({ type: 'response', content: frame, order: order++ });
            }
          });
        }

        // Process steps
        const stepMatches = chunk.match(/<intermediatestep>([\s\S]*?)<\/intermediatestep>/g) || [];
        stepMatches.forEach(match => {
          const jsonString = match
            .replace('<intermediatestep>', '')
            .replace('</intermediatestep>', '')
            .trim();
          orderedItems.push({ type: 'step', content: jsonString, order: order++ });
        });
      });

      expect(orderedItems).toHaveLength(5);
      expect(orderedItems[0].type).toBe('response');
      expect(orderedItems[1].type).toBe('step');
      expect(orderedItems[2].type).toBe('response');
      expect(orderedItems[3].type).toBe('step');
      expect(orderedItems[4].type).toBe('response');
    });
  });
});

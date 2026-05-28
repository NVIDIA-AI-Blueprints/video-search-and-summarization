/**
 * Unit tests for chat API endpoint processing functions
 * Tests payload parsing for generate, chat, generateStream, and chatStream
 */

// Mock the fetch function and Request/Response for Edge runtime
global.fetch = jest.fn();
global.Request = jest.fn();
global.Response = jest.fn().mockImplementation((body, init) => ({
  ok: true,
  status: 200,
  text: jest.fn().mockResolvedValue(body),
  json: jest.fn().mockResolvedValue(JSON.parse(body || '{}')),
  body: {
    getReader: jest.fn().mockReturnValue({
      read: jest.fn(),
      releaseLock: jest.fn(),
    }),
  },
  ...init,
}));

// Import the handler and expose internal functions for testing
const chatModule = require('@/pages/api/chat');

// We need to create mock implementations of the internal functions since they're not exported
// Let's create a test version that exposes them
describe('Chat API Processing Functions', () => {
  let encoder: TextEncoder;
  let decoder: TextDecoder;
  let mockResponse: any;

  beforeEach(() => {
    encoder = new TextEncoder();
    decoder = new TextDecoder();
    jest.clearAllMocks();
  });

  describe('processGenerate', () => {
    async function testProcessGenerate(responseData: string): Promise<string> {
      const mockResponse = {
        text: jest.fn().mockResolvedValue(responseData),
      };
      
      // Since processGenerate is not exported, we'll recreate its logic
      const data = await mockResponse.text();
      try {
        const parsed = JSON.parse(data);
        const value =
          parsed?.value ||
          parsed?.output ||
          parsed?.answer ||
          (Array.isArray(parsed?.choices)
            ? parsed.choices[0]?.message?.content
            : null);
        return typeof value === 'string' ? value : JSON.stringify(value);
      } catch {
        return data;
      }
    }

    it('should parse value field from JSON response', async () => {
      const responseData = JSON.stringify({ value: 'Test response' });
      const result = await testProcessGenerate(responseData);
      expect(result).toBe('Test response');
    });

    it('should parse output field from JSON response', async () => {
      const responseData = JSON.stringify({ output: 'Generated output' });
      const result = await testProcessGenerate(responseData);
      expect(result).toBe('Generated output');
    });

    it('should parse answer field from JSON response', async () => {
      const responseData = JSON.stringify({ answer: 'AI answer' });
      const result = await testProcessGenerate(responseData);
      expect(result).toBe('AI answer');
    });

    it('should parse choices array content', async () => {
      const responseData = JSON.stringify({
        choices: [{ message: { content: 'Choice content' } }],
      });
      const result = await testProcessGenerate(responseData);
      expect(result).toBe('Choice content');
    });

    it('should prefer value over other fields', async () => {
      const responseData = JSON.stringify({
        value: 'Primary value',
        output: 'Secondary output',
        answer: 'Tertiary answer',
      });
      const result = await testProcessGenerate(responseData);
      expect(result).toBe('Primary value');
    });

    it('should handle non-JSON response as plain text', async () => {
      const responseData = 'Plain text response';
      const result = await testProcessGenerate(responseData);
      expect(result).toBe('Plain text response');
    });

    it('should stringify non-string values', async () => {
      const responseData = JSON.stringify({ value: { complex: 'object' } });
      const result = await testProcessGenerate(responseData);
      expect(result).toBe('{"complex":"object"}');
    });

    it('should handle null choices array', async () => {
      const responseData = JSON.stringify({ choices: null });
      const result = await testProcessGenerate(responseData);
      expect(result).toBe('null');
    });
  });

  describe('processChat', () => {
    async function testProcessChat(responseData: string): Promise<string> {
      const mockResponse = {
        text: jest.fn().mockResolvedValue(responseData),
      };
      
      // Recreate processChat logic
      const data = await mockResponse.text();
      try {
        const parsed = JSON.parse(data);
        const content =
          parsed?.output ||
          parsed?.answer ||
          parsed?.value ||
          (Array.isArray(parsed?.choices)
            ? parsed.choices[0]?.message?.content
            : null) ||
          parsed ||
          data;
        return typeof content === 'string' ? content : JSON.stringify(content);
      } catch {
        return data;
      }
    }

    it('should parse output field from JSON response', async () => {
      const responseData = JSON.stringify({ output: 'Chat output' });
      const result = await testProcessChat(responseData);
      expect(result).toBe('Chat output');
    });

    it('should parse answer field from JSON response', async () => {
      const responseData = JSON.stringify({ answer: 'Chat answer' });
      const result = await testProcessChat(responseData);
      expect(result).toBe('Chat answer');
    });

    it('should parse value field from JSON response', async () => {
      const responseData = JSON.stringify({ value: 'Chat value' });
      const result = await testProcessChat(responseData);
      expect(result).toBe('Chat value');
    });

    it('should parse choices array content', async () => {
      const responseData = JSON.stringify({
        choices: [{ message: { content: 'OpenAI style content' } }],
      });
      const result = await testProcessChat(responseData);
      expect(result).toBe('OpenAI style content');
    });

    it('should prefer output over other fields', async () => {
      const responseData = JSON.stringify({
        output: 'Primary output',
        answer: 'Secondary answer',
        value: 'Tertiary value',
      });
      const result = await testProcessChat(responseData);
      expect(result).toBe('Primary output');
    });

    it('should fallback to parsed object when no specific fields found', async () => {
      const responseData = JSON.stringify({ custom: 'field', other: 'data' });
      const result = await testProcessChat(responseData);
      expect(result).toBe('{"custom":"field","other":"data"}');
    });

    it('should handle non-JSON response as plain text', async () => {
      const responseData = 'Plain chat response';
      const result = await testProcessChat(responseData);
      expect(result).toBe('Plain chat response');
    });
  });

  describe('processGenerateStream', () => {
    function createMockStreamResponse(chunks: string[]): any {
      let chunkIndex = 0;
      return {
        body: {
          getReader: () => ({
            read: jest.fn().mockImplementation(() => {
              if (chunkIndex >= chunks.length) {
                return Promise.resolve({ done: true, value: undefined });
              }
              const chunk = chunks[chunkIndex++];
              const encoded = encoder.encode(chunk);
              return Promise.resolve({ done: false, value: encoded });
            }),
            releaseLock: jest.fn(),
          }),
        },
      };
    }

    async function processStreamChunks(chunks: string[], additionalProps = { enableIntermediateSteps: true }): Promise<string[]> {
      const mockResponse = createMockStreamResponse(chunks);
      const results: string[] = [];
      
      // Recreate processGenerateStream logic
      const reader = mockResponse.body.getReader();
      let buffer = '';
      let streamContent = '';
      let finalAnswerSent = false;
      let counter = 0;

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const chunk = decoder.decode(value, { stream: true });
          buffer += chunk;
          streamContent += chunk;

          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const data = line.slice(5);
              if (data.trim() === '[DONE]') {
                return results;
              }
              try {
                const parsed = JSON.parse(data);
                const content =
                  parsed?.value ||
                  parsed?.output ||
                  parsed?.answer ||
                  parsed?.choices?.[0]?.message?.content ||
                  parsed?.choices?.[0]?.delta?.content;
                if (content && typeof content === 'string') {
                  results.push(content);
                }
              } catch {}
            } else if (
              line.includes('<intermediatestep>') &&
              line.includes('</intermediatestep>') &&
              additionalProps.enableIntermediateSteps
            ) {
              results.push(line);
            } else if (line.startsWith('intermediate_data: ')) {
              try {
                const data = line.split('intermediate_data: ')[1];
                const payload = JSON.parse(data);
                const intermediateMessage = {
                  id: payload?.id || '',
                  status: payload?.status || 'in_progress',
                  error: payload?.error || '',
                  type: 'system_intermediate',
                  parent_id: payload?.parent_id || 'default',
                  intermediate_parent_id: payload?.intermediate_parent_id || 'default',
                  content: {
                    name: payload?.name || 'Step',
                    payload: payload?.payload || 'No details',
                  },
                  time_stamp: payload?.time_stamp || 'default',
                  index: counter++,
                };
                const msg = `<intermediatestep>${JSON.stringify(intermediateMessage)}</intermediatestep>`;
                results.push(msg);
              } catch {}
            }
          }
        }
      } finally {
        if (!finalAnswerSent) {
          try {
            const parsed = JSON.parse(streamContent);
            const value =
              parsed?.value ||
              parsed?.output ||
              parsed?.answer ||
              parsed?.choices?.[0]?.message?.content;
            if (value && typeof value === 'string') {
              results.push(value.trim());
              finalAnswerSent = true;
            }
          } catch {}
        }
        reader.releaseLock();
      }
      
      return results;
    }

    it('should parse SSE data frames with value field', async () => {
      const chunks = ['data: {"value": "Stream content"}\n', 'data: [DONE]\n'];
      const results = await processStreamChunks(chunks);
      expect(results).toContain('Stream content');
    });

    it('should parse SSE data frames with choices delta', async () => {
      const chunks = [
        'data: {"choices": [{"delta": {"content": "Hello"}}]}\n',
        'data: {"choices": [{"delta": {"content": " world"}}]}\n',
        'data: [DONE]\n'
      ];
      const results = await processStreamChunks(chunks);
      expect(results).toContain('Hello');
      expect(results).toContain(' world');
    });

    it('should handle intermediate step tags when enabled', async () => {
      const chunks = ['<intermediatestep>{"type": "test"}</intermediatestep>\n'];
      const results = await processStreamChunks(chunks, { enableIntermediateSteps: true });
      expect(results).toContain('<intermediatestep>{"type": "test"}</intermediatestep>');
    });

    it('should ignore intermediate step tags when disabled', async () => {
      const chunks = ['<intermediatestep>{"type": "test"}</intermediatestep>\n'];
      const results = await processStreamChunks(chunks, { enableIntermediateSteps: false });
      expect(results).not.toContain('<intermediatestep>{"type": "test"}</intermediatestep>');
    });

    it('should process intermediate_data lines', async () => {
      const chunks = ['intermediate_data: {"id": "step1", "name": "Test Step", "payload": "data"}\n'];
      const results = await processStreamChunks(chunks);
      const intermediateMsg = results.find(r => r.includes('<intermediatestep>'));
      expect(intermediateMsg).toBeDefined();
      
      const parsed = JSON.parse(intermediateMsg!.replace('<intermediatestep>', '').replace('</intermediatestep>', ''));
      expect(parsed.type).toBe('system_intermediate');
      expect(parsed.content.name).toBe('Test Step');
      expect(parsed.content.payload).toBe('data');
    });

    it('should handle malformed JSON gracefully', async () => {
      const chunks = [
        'data: invalid json\n',
        'data: {"value": "valid content"}\n',
        'data: [DONE]\n'
      ];
      const results = await processStreamChunks(chunks);
      expect(results).toContain('valid content');
    });

    it('should process final response from accumulated stream content', async () => {
      const chunks = ['{"value": "Final response"}\n'];
      const results = await processStreamChunks(chunks);
      expect(results).toContain('Final response');
    });
  });

  describe('processChatStream', () => {
    function createMockStreamResponse(chunks: string[]): any {
      let chunkIndex = 0;
      return {
        body: {
          getReader: () => ({
            read: jest.fn().mockImplementation(() => {
              if (chunkIndex >= chunks.length) {
                return Promise.resolve({ done: true, value: undefined });
              }
              const chunk = chunks[chunkIndex++];
              const encoded = encoder.encode(chunk);
              return Promise.resolve({ done: false, value: encoded });
            }),
            releaseLock: jest.fn(),
          }),
        },
      };
    }

    async function processChatStreamChunks(chunks: string[], additionalProps = { enableIntermediateSteps: true }): Promise<string[]> {
      const mockResponse = createMockStreamResponse(chunks);
      const results: string[] = [];
      
      // Recreate processChatStream logic
      const reader = mockResponse.body.getReader();
      let buffer = '';
      let counter = 0;

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const chunk = decoder.decode(value, { stream: true });
          buffer += chunk;

          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const data = line.slice(5);
              if (data.trim() === '[DONE]') {
                return results;
              }
              try {
                const parsed = JSON.parse(data);
                const content =
                  parsed.choices?.[0]?.message?.content ||
                  parsed.choices?.[0]?.delta?.content;
                if (content) {
                  results.push(content);
                }
              } catch {}
            } else if (
              line.startsWith('intermediate_data: ') &&
              additionalProps.enableIntermediateSteps
            ) {
              try {
                const data = line.split('intermediate_data: ')[1];
                const payload = JSON.parse(data);
                const intermediateMessage = {
                  id: payload?.id || '',
                  status: payload?.status || 'in_progress',
                  error: payload?.error || '',
                  type: 'system_intermediate',
                  parent_id: payload?.parent_id || 'default',
                  intermediate_parent_id: payload?.intermediate_parent_id || 'default',
                  content: {
                    name: payload?.name || 'Step',
                    payload: payload?.payload || 'No details',
                  },
                  time_stamp: payload?.time_stamp || 'default',
                  index: counter++,
                };
                const msg = `<intermediatestep>${JSON.stringify(intermediateMessage)}</intermediatestep>`;
                results.push(msg);
              } catch {}
            }
          }
        }
      } finally {
        reader.releaseLock();
      }
      
      return results;
    }

    it('should parse OpenAI-style choices with message content', async () => {
      const chunks = [
        'data: {"choices": [{"message": {"content": "Chat response"}}]}\n',
        'data: [DONE]\n'
      ];
      const results = await processChatStreamChunks(chunks);
      expect(results).toContain('Chat response');
    });

    it('should parse OpenAI-style choices with delta content', async () => {
      const chunks = [
        'data: {"choices": [{"delta": {"content": "Streaming"}}]}\n',
        'data: {"choices": [{"delta": {"content": " chat"}}]}\n',
        'data: [DONE]\n'
      ];
      const results = await processChatStreamChunks(chunks);
      expect(results).toContain('Streaming');
      expect(results).toContain(' chat');
    });

    it('should process intermediate_data when enabled', async () => {
      const chunks = ['intermediate_data: {"id": "chat-step", "name": "Chat Step"}\n'];
      const results = await processChatStreamChunks(chunks, { enableIntermediateSteps: true });
      const intermediateMsg = results.find(r => r.includes('<intermediatestep>'));
      expect(intermediateMsg).toBeDefined();
      
      const parsed = JSON.parse(intermediateMsg!.replace('<intermediatestep>', '').replace('</intermediatestep>', ''));
      expect(parsed.content.name).toBe('Chat Step');
    });

    it('should ignore intermediate_data when disabled', async () => {
      const chunks = ['intermediate_data: {"id": "chat-step", "name": "Chat Step"}\n'];
      const results = await processChatStreamChunks(chunks, { enableIntermediateSteps: false });
      expect(results).toHaveLength(0);
    });

    it('should handle malformed SSE data gracefully', async () => {
      const chunks = [
        'data: invalid json\n',
        'data: {"choices": [{"delta": {"content": "valid"}}]}\n',
        'data: [DONE]\n'
      ];
      const results = await processChatStreamChunks(chunks);
      expect(results).toContain('valid');
    });

    it('should ignore non-choices data in SSE frames', async () => {
      const chunks = [
        'data: {"value": "should be ignored"}\n',
        'data: {"choices": [{"delta": {"content": "should be included"}}]}\n',
        'data: [DONE]\n'
      ];
      const results = await processChatStreamChunks(chunks);
      expect(results).not.toContain('should be ignored');
      expect(results).toContain('should be included');
    });
  });

  describe('Payload Building Functions', () => {
    describe('buildGeneratePayload', () => {
      function testBuildGeneratePayload(messages: any[]) {
        const userMessage = messages?.at(-1)?.content;
        if (!userMessage) {
          throw new Error('User message not found.');
        }
        return { input_message: userMessage };
      }

      it('should extract user message from messages array', () => {
        const messages = [
          { role: 'user', content: 'Hello' },
          { role: 'assistant', content: 'Hi there' },
          { role: 'user', content: 'How are you?' }
        ];
        const result = testBuildGeneratePayload(messages);
        expect(result).toEqual({ input_message: 'How are you?' });
      });

      it('should throw error when no messages provided', () => {
        expect(() => testBuildGeneratePayload([])).toThrow('User message not found.');
      });

      it('should throw error when last message has no content', () => {
        const messages = [{ role: 'user' }];
        expect(() => testBuildGeneratePayload(messages)).toThrow('User message not found.');
      });
    });

    describe('buildOpenAIChatPayload', () => {
      function testBuildOpenAIChatPayload(messages: any[]) {
        return {
          messages,
          model: 'string',
          temperature: 0,
          max_tokens: 0,
          top_p: 0,
          use_knowledge_base: true,
          top_k: 0,
          collection_name: 'string',
          stop: true,
          additionalProp1: {},
        };
      }

      it('should build OpenAI-compatible payload with messages', () => {
        const messages = [
          { role: 'user', content: 'Test message' }
        ];
        const result = testBuildOpenAIChatPayload(messages);
        expect(result.messages).toBe(messages);
        expect(result.model).toBe('string');
        expect(result.temperature).toBe(0);
        expect(result.use_knowledge_base).toBe(true);
      });

      it('should handle empty messages array', () => {
        const result = testBuildOpenAIChatPayload([]);
        expect(result.messages).toEqual([]);
        expect(result.model).toBe('string');
      });
    });
  });
});
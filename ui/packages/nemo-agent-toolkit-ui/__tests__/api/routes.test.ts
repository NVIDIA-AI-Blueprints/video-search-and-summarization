/**
 * Unit tests for proxy request transformers and response processors
 * Tests payload building and response processing for all endpoints
 */

// Import actual implementations
const {
  buildGeneratePayload,
  buildGenerateStreamPayload,
  buildChatPayload,
  buildChatStreamPayload,
  parseOptionalParams,
} = require('../../proxy/request-transformers');

const {
  processGenerate,
  processGenerateStream,
  processChat,
  processChatStream,
  processCaRag,
} = require('../../proxy/response-processors');

// Mock the fetch function for buildContextAwareRAGPayload tests
global.fetch = jest.fn();

describe('Proxy Request Transformers and Response Processors', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  describe('Request Payload Builders', () => {
    describe('parseOptionalParams', () => {
      it('should parse JSON string format', () => {
        const result = parseOptionalParams('{"temperature": 0.8, "max_tokens": 100}');
        expect(result).toEqual({ temperature: 0.8, max_tokens: 100 });
      });

      it('should parse key=value comma-separated format', () => {
        const result = parseOptionalParams('temperature=0.9,max_tokens=200');
        expect(result).toEqual({ temperature: 0.9, max_tokens: 200 });
      });

      it('should parse boolean values', () => {
        const result = parseOptionalParams('echo=true,stream=false');
        expect(result).toEqual({ echo: true, stream: false });
      });

      it('should return empty object for empty string', () => {
        const result = parseOptionalParams('');
        expect(result).toEqual({});
      });

      it('should return empty object for whitespace', () => {
        const result = parseOptionalParams('   ');
        expect(result).toEqual({});
      });
    });

    describe('buildGeneratePayload', () => {
      it('should build payload with input_message', () => {
        const result = buildGeneratePayload('Hello world');
        expect(result).toEqual({ input_message: 'Hello world' });
      });

      it('should handle empty string', () => {
        const result = buildGeneratePayload('');
        expect(result).toEqual({ input_message: '' });
      });

      it('should handle undefined with empty string fallback', () => {
        const result = buildGeneratePayload(undefined);
        expect(result).toEqual({ input_message: '' });
      });
    });

    describe('buildGenerateStreamPayload', () => {
      it('should build payload with input_message', () => {
        const result = buildGenerateStreamPayload('Stream this message');
        expect(result).toEqual({ input_message: 'Stream this message' });
      });

      it('should handle empty string', () => {
        const result = buildGenerateStreamPayload('');
        expect(result).toEqual({ input_message: '' });
      });
    });

    describe('buildChatPayload', () => {
      it('should build payload with stream: false', () => {
        const messages = [
          { role: 'user', content: 'Hello' },
          { role: 'assistant', content: 'Hi' },
          { role: 'user', content: 'How are you?' }
        ];
        const result = buildChatPayload(messages, true, '');
        
        expect(result.stream).toBe(false);
        expect(result.messages).toEqual(messages);
        expect(result.model).toBe('nvidia/nemotron');
        expect(result.temperature).toBe(0.7);
      });

      it('should use only last message when useChatHistory is false', () => {
        const messages = [
          { role: 'user', content: 'Hello' },
          { role: 'assistant', content: 'Hi' },
          { role: 'user', content: 'How are you?' }
        ];
        const result = buildChatPayload(messages, false, '');
        
        expect(result.messages).toEqual([{ role: 'user', content: 'How are you?' }]);
      });

      it('should merge optional parameters', () => {
        const messages = [{ role: 'user', content: 'Test' }];
        const result = buildChatPayload(messages, true, '{"max_tokens": 500, "top_p": 0.9}');
        
        expect(result.max_tokens).toBe(500);
        expect(result.top_p).toBe(0.9);
        expect(result.stream).toBe(false);
      });

      it('should use key=value format for optional params', () => {
        const messages = [{ role: 'user', content: 'Test' }];
        const result = buildChatPayload(messages, true, 'max_tokens=300,top_p=0.95');
        
        expect(result.max_tokens).toBe(300);
        expect(result.top_p).toBe(0.95);
      });

      it('should enforce server-side filtering of reserved fields', () => {
        // Server-side enforcement: Even if UI validation is bypassed, the server
        // filters out reserved fields (messages, stream) from optionalParams
        const messages = [{ role: 'user', content: 'Test' }];
        
        // Attempt to override reserved field 'stream' via optionalParams
        const result = buildChatPayload(messages, true, '{"stream": true, "max_tokens": 100}');
        
        // Server enforces: 'stream' is filtered out, remains false
        expect(result.stream).toBe(false);
        // Non-reserved fields are still merged
        expect(result.max_tokens).toBe(100);
      });

      it('should filter out messages field from optionalParams', () => {
        const messages = [
          { role: 'user', content: 'Hello' },
          { role: 'assistant', content: 'Hi' },
        ];
        
        // Attempt to override 'messages' via optionalParams
        const result = buildChatPayload(
          messages,
          true,
          '{"messages": [{"role": "system", "content": "Override"}], "temperature": 0.9}'
        );
        
        // Server enforces: 'messages' is filtered out, original messages preserved
        expect(result.messages).toEqual(messages);
        // Non-reserved fields are still merged
        expect(result.temperature).toBe(0.9);
      });

      it('should handle parse errors in optionalParams gracefully', () => {
        const messages = [{ role: 'user', content: 'Test' }];
        
        // Invalid JSON should not crash, just use defaults
        const result = buildChatPayload(messages, true, '{invalid json}');
        
        expect(result.stream).toBe(false);
        expect(result.model).toBe('nvidia/nemotron');
        expect(result.temperature).toBe(0.7);
      });
    });

    describe('buildChatStreamPayload', () => {
      it('should build payload with stream: true', () => {
        const messages = [
          { role: 'user', content: 'Hello' },
          { role: 'assistant', content: 'Hi' },
          { role: 'user', content: 'How are you?' }
        ];
        const result = buildChatStreamPayload(messages, true, '');
        
        expect(result.stream).toBe(true);
        expect(result.messages).toEqual(messages);
        expect(result.model).toBe('nvidia/nemotron');
        expect(result.temperature).toBe(0.7);
      });

      it('should use only last message when useChatHistory is false', () => {
        const messages = [
          { role: 'user', content: 'Hello' },
          { role: 'assistant', content: 'Hi' },
          { role: 'user', content: 'How are you?' }
        ];
        const result = buildChatStreamPayload(messages, false, '');
        
        expect(result.messages).toEqual([{ role: 'user', content: 'How are you?' }]);
      });

      it('should merge optional parameters', () => {
        const messages = [{ role: 'user', content: 'Test' }];
        const result = buildChatStreamPayload(messages, true, '{"max_tokens": 500, "top_p": 0.9}');
        
        expect(result.max_tokens).toBe(500);
        expect(result.top_p).toBe(0.9);
        expect(result.stream).toBe(true);
      });

      it('should use key=value format for optional params', () => {
        const messages = [{ role: 'user', content: 'Test' }];
        const result = buildChatStreamPayload(messages, true, 'max_tokens=300,top_p=0.95');
        
        expect(result.max_tokens).toBe(300);
        expect(result.top_p).toBe(0.95);
      });

      it('should enforce server-side filtering of reserved fields', () => {
        // Server-side enforcement: Even if UI validation is bypassed, the server
        // filters out reserved fields (messages, stream) from optionalParams
        const messages = [{ role: 'user', content: 'Test' }];
        
        // Attempt to override reserved field 'stream' via optionalParams
        const result = buildChatStreamPayload(messages, true, '{"stream": false, "max_tokens": 100}');
        
        // Server enforces: 'stream' is filtered out, remains true (critical fix)
        expect(result.stream).toBe(true);
        // Non-reserved fields are still merged
        expect(result.max_tokens).toBe(100);
      });

      it('should filter out messages field from optionalParams', () => {
        const messages = [
          { role: 'user', content: 'Hello' },
          { role: 'assistant', content: 'Hi' },
        ];
        
        // Attempt to override 'messages' via optionalParams
        const result = buildChatStreamPayload(
          messages,
          true,
          '{"messages": [{"role": "system", "content": "Override"}], "temperature": 0.8}'
        );
        
        // Server enforces: 'messages' is filtered out, original messages preserved
        expect(result.messages).toEqual(messages);
        // Non-reserved fields are still merged
        expect(result.temperature).toBe(0.8);
      });

      it('should always have stream: true (critical fix)', () => {
        const messages = [{ role: 'user', content: 'Test' }];
        const result = buildChatStreamPayload(messages, true, '');
 
        // Critical: buildChatStreamPayload MUST always set stream: true for SSE
        expect(result.stream).toBe(true);
      });

      it('should handle parse errors in optionalParams gracefully', () => {
        const messages = [{ role: 'user', content: 'Test' }];
        
        // Invalid JSON should not crash, just use defaults
        const result = buildChatStreamPayload(messages, true, '{invalid json}');
        
        expect(result.stream).toBe(true);
        expect(result.model).toBe('nvidia/nemotron');
        expect(result.temperature).toBe(0.7);
      });
    });

    describe('buildContextAwareRAGPayload', () => {
      let mockFetch: jest.Mock;

      beforeEach(() => {
        mockFetch = global.fetch as jest.Mock;
        mockFetch.mockClear();
      });

      function createBuildContextAwareRAGPayload() {
        // Track initialized conversations to avoid re-initialization
        const initializedConversations = new Set<string>();

        return async (messages: any[], conversationId: string, serverURL: string) => {
          if (!messages?.length || messages[messages.length - 1]?.role !== 'user') {
            throw new Error('User message not found: messages array is empty or invalid.');
          }

          // Initialize the retrieval system only once per conversation
          const ragUuid = '123456'; // Use a fixed value for testing
          const combinedConversationId = `${ragUuid}-${conversationId || 'default'}`;

          if (!initializedConversations.has(combinedConversationId)) {
            try {
              const initResponse = await fetch(`${serverURL}/init`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uuid: ragUuid }),
              });

              if (!initResponse.ok) {
                throw new Error(`CA RAG initialization failed: ${initResponse.statusText}`);
              }

              initializedConversations.add(combinedConversationId);
            } catch (initError) {
              throw new Error(`CA RAG initialization failed: ${initError instanceof Error ? initError.message : 'Unknown error'}`);
            }
          }

          return {
            state: {
              chat: {
                question: messages[messages.length - 1]?.content ?? ''
              }
            }
          };
        };
      }

      it('should build payload with question from last message', async () => {
        const buildPayload = createBuildContextAwareRAGPayload();
        mockFetch.mockResolvedValueOnce({ ok: true });

        const messages = [
          { role: 'user', content: 'First question' },
          { role: 'assistant', content: 'Answer' },
          { role: 'user', content: 'Second question' }
        ];

        const result = await buildPayload(messages, 'conv-123', 'http://localhost:8080');

        expect(result).toEqual({
          state: {
            chat: {
              question: 'Second question'
            }
          }
        });
      });

      it('should call init endpoint on first use for a conversation', async () => {
        const buildPayload = createBuildContextAwareRAGPayload();
        mockFetch.mockResolvedValueOnce({ ok: true });

        const messages = [{ role: 'user', content: 'Test question' }];

        await buildPayload(messages, 'conv-123', 'http://localhost:8080');

        expect(mockFetch).toHaveBeenCalledWith(
          'http://localhost:8080/init',
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ uuid: '123456' })
          }
        );
      });

      it('should not call init endpoint on subsequent uses for same conversation', async () => {
        const buildPayload = createBuildContextAwareRAGPayload();
        mockFetch.mockResolvedValue({ ok: true });

        const messages = [{ role: 'user', content: 'Test question' }];

        // First call - should initialize
        await buildPayload(messages, 'conv-123', 'http://localhost:8080');
        expect(mockFetch).toHaveBeenCalledTimes(1);

        mockFetch.mockClear();

        // Second call - should NOT initialize again
        await buildPayload(messages, 'conv-123', 'http://localhost:8080');
        expect(mockFetch).not.toHaveBeenCalled();
      });

      it('should call init endpoint for different conversations', async () => {
        const buildPayload = createBuildContextAwareRAGPayload();
        mockFetch.mockResolvedValue({ ok: true });

        const messages = [{ role: 'user', content: 'Test question' }];

        // First conversation
        await buildPayload(messages, 'conv-123', 'http://localhost:8080');
        expect(mockFetch).toHaveBeenCalledTimes(1);

        mockFetch.mockClear();

        // Different conversation - should initialize
        await buildPayload(messages, 'conv-456', 'http://localhost:8080');
        expect(mockFetch).toHaveBeenCalledTimes(1);
      });

      it('should throw error when messages array is empty', async () => {
        const buildPayload = createBuildContextAwareRAGPayload();

        await expect(
          buildPayload([], 'conv-123', 'http://localhost:8080')
        ).rejects.toThrow('User message not found: messages array is empty or invalid.');
      });

      it('should throw error when last message is not from user', async () => {
        const buildPayload = createBuildContextAwareRAGPayload();

        const messages = [
          { role: 'user', content: 'Question' },
          { role: 'assistant', content: 'Answer' }
        ];

        await expect(
          buildPayload(messages, 'conv-123', 'http://localhost:8080')
        ).rejects.toThrow('User message not found: messages array is empty or invalid.');
      });

      it('should throw error when init endpoint fails', async () => {
        const buildPayload = createBuildContextAwareRAGPayload();
        mockFetch.mockResolvedValueOnce({ ok: false, statusText: 'Internal Server Error' });

        const messages = [{ role: 'user', content: 'Test question' }];

        await expect(
          buildPayload(messages, 'conv-123', 'http://localhost:8080')
        ).rejects.toThrow('CA RAG initialization failed: Internal Server Error');
      });

      it('should throw error when init endpoint network fails', async () => {
        const buildPayload = createBuildContextAwareRAGPayload();
        mockFetch.mockRejectedValueOnce(new Error('Network error'));

        const messages = [{ role: 'user', content: 'Test question' }];

        await expect(
          buildPayload(messages, 'conv-123', 'http://localhost:8080')
        ).rejects.toThrow('CA RAG initialization failed: Network error');
      });

      it('should use default conversation ID when not provided', async () => {
        const buildPayload = createBuildContextAwareRAGPayload();
        mockFetch.mockResolvedValue({ ok: true });

        const messages = [{ role: 'user', content: 'Test question' }];

        // Call with empty string
        await buildPayload(messages, '', 'http://localhost:8080');
        expect(mockFetch).toHaveBeenCalledTimes(1);

        mockFetch.mockClear();

        // Call again with empty string - should NOT initialize (same as default)
        await buildPayload(messages, '', 'http://localhost:8080');
        expect(mockFetch).not.toHaveBeenCalled();
      });

      it('should handle empty content in last message', async () => {
        const buildPayload = createBuildContextAwareRAGPayload();
        mockFetch.mockResolvedValueOnce({ ok: true });

        const messages = [{ role: 'user', content: '' }];

        const result = await buildPayload(messages, 'conv-123', 'http://localhost:8080');

        expect(result).toEqual({
          state: {
            chat: {
              question: ''
            }
          }
        });
      });
    });
  });

  describe('Response Processors', () => {
    describe('processGenerate', () => {
      it('should process JSON response with value field', async () => {
        const mockBackendRes = {
          ok: true,
          text: jest.fn().mockResolvedValue('{"value":"Test response"}'),
          headers: {
            get: jest.fn().mockReturnValue(null),
          },
        };
        
        const mockRes = {
          writeHead: jest.fn(),
          end: jest.fn(),
        };

        await processGenerate(mockBackendRes, mockRes);

        expect(mockRes.writeHead).toHaveBeenCalledWith(200, expect.objectContaining({
          'Content-Type': 'application/json; charset=utf-8',
        }));
        expect(mockRes.end).toHaveBeenCalledWith('{"value":"Test response"}');
      });

      it('should handle non-ok response', async () => {
        const mockBackendRes = {
          ok: false,
          status: 500,
          statusText: 'Internal Server Error',
          text: jest.fn().mockResolvedValue('Internal Server Error'),
        };
        
        const mockRes = {
          writeHead: jest.fn(),
          end: jest.fn(),
        };

        await processGenerate(mockBackendRes, mockRes);

        expect(mockRes.writeHead).toHaveBeenCalledWith(500, expect.any(Object));
        expect(mockRes.end).toHaveBeenCalledWith('Internal Server Error');
      });

      it('should forward observability-trace-id header from backend', async () => {
        const mockBackendRes = {
          ok: true,
          text: jest.fn().mockResolvedValue('{"value":"Test response"}'),
          headers: {
            get: jest.fn((name) => {
              if (name === 'observability-trace-id') return 'trace-header-123';
              return null;
            }),
          },
        };
        
        const mockRes = {
          writeHead: jest.fn(),
          end: jest.fn(),
        };

        await processGenerate(mockBackendRes, mockRes);

        expect(mockRes.writeHead).toHaveBeenCalledWith(200, expect.objectContaining({
          'Observability-Trace-Id': 'trace-header-123',
        }));
      });
    });

    describe('processChat', () => {
      it('should process JSON response', async () => {
        const mockBackendRes = {
          ok: true,
          text: jest.fn().mockResolvedValue('{"choices":[{"message":{"content":"Chat response"}}]}'),
          headers: {
            get: jest.fn().mockReturnValue(null),
          },
        };
        
        const mockRes = {
          writeHead: jest.fn(),
          end: jest.fn(),
        };

        await processChat(mockBackendRes, mockRes);

        expect(mockRes.writeHead).toHaveBeenCalledWith(200, expect.any(Object));
        expect(mockRes.end).toHaveBeenCalled();
      });

      it('should handle non-ok response', async () => {
        const mockBackendRes = {
          ok: false,
          status: 400,
          statusText: 'Bad Request',
          text: jest.fn().mockResolvedValue('Bad Request'),
        };
        
        const mockRes = {
          writeHead: jest.fn(),
          end: jest.fn(),
        };

        await processChat(mockBackendRes, mockRes);

        expect(mockRes.writeHead).toHaveBeenCalledWith(400, expect.any(Object));
        expect(mockRes.end).toHaveBeenCalledWith('Bad Request');
      });

      it('should forward observability-trace-id header from backend', async () => {
        const mockBackendRes = {
          ok: true,
          text: jest.fn().mockResolvedValue('{"choices":[{"message":{"content":"Chat response"}}]}'),
          headers: {
            get: jest.fn((name) => {
              if (name === 'observability-trace-id') return 'trace-chat-456';
              return null;
            }),
          },
        };
        
        const mockRes = {
          writeHead: jest.fn(),
          end: jest.fn(),
        };

        await processChat(mockBackendRes, mockRes);

        expect(mockRes.writeHead).toHaveBeenCalledWith(200, expect.objectContaining({
          'Observability-Trace-Id': 'trace-chat-456',
        }));
      });
    });

    describe('processChatStream', () => {
      function createStreamingResponse(chunks) {
        const encoder = new TextEncoder();
        let chunkIndex = 0;
        
        return {
          ok: true,
          body: {
            getReader: () => ({
              read: jest.fn().mockImplementation(() => {
                if (chunkIndex >= chunks.length) {
                  return Promise.resolve({ done: true, value: undefined });
                }
                const chunk = chunks[chunkIndex++];
                return Promise.resolve({ done: false, value: encoder.encode(chunk) });
              }),
            }),
          },
        };
      }

      it('should process SSE stream with chat data', async () => {
        const mockBackendRes = createStreamingResponse([
          'data: {"choices":[{"delta":{"content":"Hello"}}]}\n',
          'data: {"choices":[{"delta":{"content":" world"}}]}\n',
        ]);
        
        const mockRes = {
          writeHead: jest.fn(),
          write: jest.fn(),
          end: jest.fn(),
        };

        await processChatStream(mockBackendRes, mockRes);

        expect(mockRes.writeHead).toHaveBeenCalledWith(200, expect.objectContaining({
          'Content-Type': expect.stringMatching(/text\/event-stream/i),
        }));
        expect(mockRes.write).toHaveBeenCalledWith('Hello');
        expect(mockRes.write).toHaveBeenCalledWith(' world');
        expect(mockRes.end).toHaveBeenCalled();
      });

      it('should process intermediate_data lines', async () => {
        const mockBackendRes = createStreamingResponse([
          'intermediate_data: {"id":"step1","name":"Test Step","payload":"data"}\n',
        ]);
        
        const mockRes = {
          writeHead: jest.fn(),
          write: jest.fn(),
          end: jest.fn(),
        };

        await processChatStream(mockBackendRes, mockRes);

        expect(mockRes.write).toHaveBeenCalledWith(expect.stringContaining('<intermediatestep>'));
        expect(mockRes.write).toHaveBeenCalledWith(expect.stringContaining('Test Step'));
      });

      it('should process observability_trace lines separately', async () => {
        const mockBackendRes = createStreamingResponse([
          'data: {"choices":[{"delta":{"content":"Response"}}]}\n',
          'observability_trace: {"observability_trace_id":"trace-abc-123"}\n',
        ]);
        
        const mockRes = {
          writeHead: jest.fn(),
          write: jest.fn(),
          end: jest.fn(),
        };

        await processChatStream(mockBackendRes, mockRes);

        // Should write both content and trace ID tag
        expect(mockRes.write).toHaveBeenCalledWith('Response');
        expect(mockRes.write).toHaveBeenCalledWith(expect.stringContaining('<observabilitytraceid>trace-abc-123</observabilitytraceid>'));
      });

      it('should handle non-ok response', async () => {
        const mockBackendRes = {
          ok: false,
          status: 502,
          statusText: 'Bad Gateway',
          text: jest.fn().mockResolvedValue('Bad Gateway'),
        };
        
        const mockRes = {
          writeHead: jest.fn(),
          end: jest.fn(),
        };

        await processChatStream(mockBackendRes, mockRes);

        expect(mockRes.writeHead).toHaveBeenCalledWith(502, expect.any(Object));
        expect(mockRes.end).toHaveBeenCalledWith('Bad Gateway');
      });
    });

    describe('processGenerateStream', () => {
      function createStreamingResponse(chunks) {
        const encoder = new TextEncoder();
        let chunkIndex = 0;
        
        return {
          ok: true,
          body: {
            getReader: () => ({
              read: jest.fn().mockImplementation(() => {
                if (chunkIndex >= chunks.length) {
                  return Promise.resolve({ done: true, value: undefined });
                }
                const chunk = chunks[chunkIndex++];
                return Promise.resolve({ done: false, value: encoder.encode(chunk) });
              }),
            }),
          },
        };
      }

      it('should process SSE stream with generate data', async () => {
        const mockBackendRes = createStreamingResponse([
          'data: {"value":"Stream"}\n',
          'data: {"value":" content"}\n',
        ]);
        
        const mockRes = {
          writeHead: jest.fn(),
          write: jest.fn(),
          end: jest.fn(),
        };

        await processGenerateStream(mockBackendRes, mockRes);

        expect(mockRes.writeHead).toHaveBeenCalledWith(200, expect.objectContaining({
          'Content-Type': expect.stringMatching(/text\/event-stream/i),
        }));
        expect(mockRes.write).toHaveBeenCalled();
        expect(mockRes.end).toHaveBeenCalled();
      });

      it('should process intermediate_data lines', async () => {
        const mockBackendRes = createStreamingResponse([
          'intermediate_data: {"id":"gen-step","name":"Generation Step"}\n',
        ]);
        
        const mockRes = {
          writeHead: jest.fn(),
          write: jest.fn(),
          end: jest.fn(),
        };

        await processGenerateStream(mockBackendRes, mockRes);

        expect(mockRes.write).toHaveBeenCalledWith(expect.stringContaining('<intermediatestep>'));
        expect(mockRes.write).toHaveBeenCalledWith(expect.stringContaining('Generation Step'));
      });

      it('should process observability_trace lines separately', async () => {
        const mockBackendRes = createStreamingResponse([
          'data: {"value":"Generated text"}\n',
          'observability_trace: {"observability_trace_id":"trace-def-456"}\n',
        ]);
        
        const mockRes = {
          writeHead: jest.fn(),
          write: jest.fn(),
          end: jest.fn(),
        };

        await processGenerateStream(mockBackendRes, mockRes);

        // Should process trace ID separately from content
        expect(mockRes.write).toHaveBeenCalledWith(expect.stringContaining('<observabilitytraceid>trace-def-456</observabilitytraceid>'));
      });

      it('should handle non-ok response', async () => {
        const mockBackendRes = {
          ok: false,
          status: 503,
          statusText: 'Service Unavailable',
          text: jest.fn().mockResolvedValue('Service Unavailable'),
        };
        
        const mockRes = {
          writeHead: jest.fn(),
          end: jest.fn(),
        };

        await processGenerateStream(mockBackendRes, mockRes);

        expect(mockRes.writeHead).toHaveBeenCalledWith(503, expect.any(Object));
        expect(mockRes.end).toHaveBeenCalledWith('Service Unavailable');
      });
    });

    describe('processCaRag', () => {
      it('should process JSON response with result field', async () => {
        const mockBackendRes = {
          ok: true,
          text: jest.fn().mockResolvedValue('{"result":"RAG response"}'),
        };
        
        const mockRes = {
          writeHead: jest.fn(),
          end: jest.fn(),
        };

        await processCaRag(mockBackendRes, mockRes);

        expect(mockRes.writeHead).toHaveBeenCalledWith(200, expect.any(Object));
        expect(mockRes.end).toHaveBeenCalled();
      });

      it('should handle non-ok response', async () => {
        const mockBackendRes = {
          ok: false,
          status: 404,
          statusText: 'Not Found',
          text: jest.fn().mockResolvedValue('Not Found'),
        };
        
        const mockRes = {
          writeHead: jest.fn(),
          end: jest.fn(),
        };

        await processCaRag(mockBackendRes, mockRes);

        expect(mockRes.writeHead).toHaveBeenCalledWith(404, expect.any(Object));
        expect(mockRes.end).toHaveBeenCalledWith('Not Found');
      });
    });
  });
});
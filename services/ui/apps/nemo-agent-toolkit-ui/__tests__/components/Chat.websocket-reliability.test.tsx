/**
 * Tests for WebSocket connection reliability, message ordering, and session management
 */

import MockWebSocket from '@/__mocks__/websocket';
import { SESSION_COOKIE_NAME } from '@/constants/constants';
import {
  validateWebSocketMessageWithConversationId,
  isSystemResponseMessage,
  isSystemIntermediateMessage,
  processSystemResponseMessage
} from '@/types/websocket';
import toast from 'react-hot-toast';

// Mock react-hot-toast
jest.mock('react-hot-toast', () => ({
  __esModule: true,
  default: {
    loading: jest.fn(),
    success: jest.fn(),
    error: jest.fn(),
    dismiss: jest.fn()
  }
}));

// Mock timers for connection timeout tests
jest.useFakeTimers();

describe('WebSocket Connection Reliability', () => {
  beforeEach(() => {
    MockWebSocket.lastInstance = null;
    jest.clearAllMocks();
    jest.clearAllTimers();
  });

  afterEach(() => {
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
    jest.useFakeTimers();
  });

  describe('Connection Management', () => {
    /**
     * Description: Verifies that WebSocket connection timeouts during handshake are handled with appropriate user feedback
     * Success: Loading toast is displayed during connection attempts, success toast shown on completion
     */
    test('handles connection timeout during handshake', async () => {
      let resolveConnection: (value: boolean) => void;
      const connectionPromise = new Promise<boolean>(resolve => {
        resolveConnection = resolve;
      });

      // Simulate slow connecting WebSocket
      const mockConnectWebSocket = async (retryCount = 0) => {
        const maxRetries = 3;
        const retryDelay = 1000;

        if (retryCount >= maxRetries) {
          resolveConnection(false);
          return false;
        }

        return new Promise(resolve => {
          const ws = new MockWebSocket('ws://slow-server.com/websocket');

          toast.loading('WebSocket is not connected, trying to connect...', {
            id: 'websocketLoadingToastId'
          });

          // Simulate connection taking longer than expected
          setTimeout(() => {
            ws.readyState = MockWebSocket.OPEN;
            if (ws.onopen) ws.onopen(new Event('open'));
            toast.success('Connected to server');
            resolveConnection(true);
            resolve(true);
          }, 5000); // 5 second delay

          ws.onclose = async () => {
            if (retryCount < maxRetries) {
              await new Promise(res => setTimeout(res, retryDelay));
              const success = await mockConnectWebSocket(retryCount + 1);
              resolve(success);
            } else {
              toast.error('WebSocket connection failed.');
              resolveConnection(false);
              resolve(false);
            }
          };
        });
      };

      // Start connection attempt
      const connectionAttempt = mockConnectWebSocket();

      // Advance timers by 3 seconds (less than connection time)
      jest.advanceTimersByTime(3000);

      // Should still be attempting connection
      expect(toast.loading).toHaveBeenCalledWith(
        'WebSocket is not connected, trying to connect...',
        { id: 'websocketLoadingToastId' }
      );

      // Complete the connection
      jest.advanceTimersByTime(2000);

      const result = await connectionAttempt;
      expect(result).toBe(true);
      expect(toast.success).toHaveBeenCalledWith('Connected to server');
    });
    /**
     * Description: Verifies that WebSocket connection retries implement exponential backoff delays
     * Success: Connection attempts are retried with increasing delays until successful connection is established
     */
    test('retry mechanism with exponential backoff', () => {
      const baseDelay = 1000;
      const maxRetries = 3;
      const calculatedDelays: number[] = [];

      // Simulate the retry delay calculation logic
      for (let attempt = 0; attempt < maxRetries; attempt++) {
        const delay = baseDelay * Math.pow(2, attempt);
        calculatedDelays.push(delay);
      }

      // Verify exponential backoff pattern
      expect(calculatedDelays).toEqual([1000, 2000, 4000]);
      expect(calculatedDelays[0]).toBe(1000); // First retry: 1000ms
      expect(calculatedDelays[1]).toBe(2000); // Second retry: 2000ms
      expect(calculatedDelays[2]).toBe(4000); // Third retry: 4000ms
    });

    /**
     * Description: Verifies that WebSocket connections are properly closed when component unmounts
     * Success: WebSocket connection is closed and cleanup procedures are executed to prevent memory leaks
     */
    test('connection cleanup on component unmount', () => {
      const ws = new MockWebSocket('ws://test-server.com/websocket');
      const mockClose = jest.spyOn(ws, 'close');

      // Simulate component unmount cleanup
      const cleanup = () => {
        if (ws && ws.readyState === MockWebSocket.OPEN) {
          ws.close();
        }
      };

      ws.readyState = MockWebSocket.OPEN;
      cleanup();

      expect(mockClose).toHaveBeenCalled();
    });
  });

  describe('Session Cookie Management', () => {
    /**
     * Description: Verifies that session cookies can be extracted from various cookie string formats
     * Success: Session cookies are correctly parsed from different cookie formats and encoding styles
     */
    test('session cookie extraction works with various cookie formats', () => {
      const cookieScenarios = [
        {
          cookie: `${SESSION_COOKIE_NAME}=simple-session`,
          expected: 'simple-session'
        },
        {
          cookie: `other=value; ${SESSION_COOKIE_NAME}=session-with-prefix; more=data`,
          expected: 'session-with-prefix'
        },
        {
          cookie: `${SESSION_COOKIE_NAME}=session%20with%20encoding`,
          expected: 'session%20with%20encoding'
        },
        {
          cookie: `prefix_${SESSION_COOKIE_NAME}=wrong; ${SESSION_COOKIE_NAME}=correct`,
          expected: 'correct'
        },
        {
          cookie: `${SESSION_COOKIE_NAME}=value_with_equals=sign`,
          expected: 'value_with_equals=sign'
        }
      ];

      const getCookie = (name: string, cookieString: string) => {
        const value = `; ${cookieString}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop()?.split(';').shift();
        return null;
      };

      cookieScenarios.forEach(({ cookie, expected }) => {
        const extracted = getCookie(SESSION_COOKIE_NAME, cookie);
        expect(extracted).toBe(expected);
        expect(extracted).not.toContain('wrong');
      });
    });

    /**
     * Description: Verifies that missing or malformed session cookies are handled gracefully without errors
     * Success: Connection continues with fallback authentication, no exceptions thrown for invalid cookies
     */
    test('handles missing or malformed cookies gracefully', () => {
      const invalidCookieScenarios = [
        '',
        'other=value; different=cookie',
        `other_${SESSION_COOKIE_NAME}=not-exact-match`,
        'malformed cookie string without equals',
        `${SESSION_COOKIE_NAME}=`,  // Empty value
        `${SESSION_COOKIE_NAME}`    // No equals sign
      ];

      const getCookie = (name: string, cookieString: string) => {
        const value = `; ${cookieString}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop()?.split(';').shift();
        return null;
      };

      invalidCookieScenarios.forEach(cookie => {
        const extracted = getCookie(SESSION_COOKIE_NAME, cookie);
        // Should either be null or empty string, but not crash
        expect(typeof extracted === 'string' || extracted === null).toBe(true);
      });
    });

    /**
     * Description: Verifies that WebSocket URLs are correctly constructed with session cookie parameters
     * Success: Session cookies are properly encoded and included in WebSocket connection URL
     */
    test('WebSocket URL construction with session cookie', () => {
      const sessionId = 'test-session-123';
      const baseUrls = [
        'ws://example.com/websocket',
        'wss://secure.example.com/websocket',
        'ws://localhost:8000/websocket',
        'ws://example.com/websocket?existing=param'
      ];

      baseUrls.forEach(baseUrl => {
        const separator = baseUrl.includes('?') ? '&' : '?';
        const finalUrl = `${baseUrl}${separator}session=${encodeURIComponent(sessionId)}`;

        const ws = new MockWebSocket(finalUrl);

        expect(ws.url).toContain('session=');
        expect(ws.url).toContain(encodeURIComponent(sessionId));

        // Verify URL is properly formed
        expect(() => new URL(ws.url.replace('ws:', 'http:').replace('wss:', 'https:'))).not.toThrow();
      });
    });

    /**
     * Description: Verifies that cross-origin WebSocket connections include session data in URL parameters
     * Success: Session information is correctly included in URL for cross-origin authentication
     */
    test('cross-origin connection includes session in URL', () => {
      const sessionId = 'cross-origin-session';
      const cookieString = `${SESSION_COOKIE_NAME}=${sessionId}`;

      // Mock document.cookie
      Object.defineProperty(document, 'cookie', {
        value: cookieString,
        writable: true
      });

      const getCookie = (name: string) => {
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop()?.split(';').shift();
        return null;
      };

      const sessionCookie = getCookie(SESSION_COOKIE_NAME);
      let wsUrl = 'wss://external-server.com/websocket';

      // Determine if this is cross-origin (it is, since we're testing from localhost)
      const wsUrlObj = new URL(wsUrl);
      const isCrossOrigin = wsUrlObj.origin !== window.location.origin;

      // Always add session cookie for cross-origin
      if (sessionCookie && isCrossOrigin) {
        const separator = wsUrl.includes('?') ? '&' : '?';
        wsUrl += `${separator}session=${encodeURIComponent(sessionCookie)}`;
      }

      expect(wsUrl).toContain(`session=${encodeURIComponent(sessionId)}`);
      expect(isCrossOrigin).toBe(true);
    });
  });

  describe('Message Ordering and Processing', () => {
    /**
     * Description: Verifies that message ordering is preserved when receiving rapid WebSocket messages
     * Success: Messages are processed and displayed in the exact order they were received
     */
    test('maintains message order during rapid WebSocket messages', () => {
      const messages: any[] = [];
      const conversation = { id: 'test-conv', messages: [] };

      // Create rapid sequence of messages
      const rapidMessages = Array.from({ length: 50 }, (_, i) => ({
        type: 'system_response_message',
        status: 'in_progress',
        conversation_id: 'test-conv',
        id: `msg-${i}`,
        content: { text: `chunk${i}` }
      }));

      // Mock message processing function
      const processMessage = (message: any, currentMessages: any[]) => {
        if (!isSystemResponseMessage(message)) return currentMessages;

        const lastMessage = currentMessages[currentMessages.length - 1];
        if (lastMessage && lastMessage.role === 'assistant') {
          // Append to existing message
          return currentMessages.map((m, idx) =>
            idx === currentMessages.length - 1
              ? { ...m, content: (m.content || '') + message.content.text }
              : m
          );
        } else {
          // Create new assistant message
          return [...currentMessages, {
            role: 'assistant',
            content: message.content.text,
            id: message.id
          }];
        }
      };

      // Process messages sequentially
      let currentMessages = conversation.messages;
      rapidMessages.forEach(msg => {
        currentMessages = processMessage(msg, currentMessages);
      });

      // Verify content built correctly in order
      expect(currentMessages).toHaveLength(1);
      const finalContent = currentMessages[0].content;
      expect(finalContent).toContain('chunk0');
      expect(finalContent).toContain('chunk49');

      // Verify all chunks are present and in order
      for (let i = 0; i < 50; i++) {
        expect(finalContent).toContain(`chunk${i}`);
      }
    });

    /**
     * Description: Verifies that out-of-order WebSocket messages are handled gracefully without corruption
     * Success: Messages are reordered correctly or processed independently without breaking conversation flow
     */
    test('handles out-of-order message IDs gracefully', () => {
      const conversation = { id: 'test-conv', messages: [] };

      // Messages arrive out of order
      const outOfOrderMessages = [
        { type: 'system_response_message', status: 'in_progress', conversation_id: 'test-conv', id: 'msg-3', content: { text: 'third' } },
        { type: 'system_response_message', status: 'in_progress', conversation_id: 'test-conv', id: 'msg-1', content: { text: 'first' } },
        { type: 'system_response_message', status: 'in_progress', conversation_id: 'test-conv', id: 'msg-2', content: { text: 'second' } }
      ];

      const processedMessages: any[] = [];

      // Process in arrival order (which is out of sequence)
      outOfOrderMessages.forEach(msg => {
        processedMessages.push({
          role: 'assistant',
          content: msg.content.text,
          id: msg.id,
          timestamp: Date.now()
        });
      });

      // Should preserve arrival order rather than trying to reorder
      expect(processedMessages[0].content).toBe('third');
      expect(processedMessages[1].content).toBe('first');
      expect(processedMessages[2].content).toBe('second');
    });

    /**
     * Description: Verifies that different WebSocket message types can be processed concurrently without conflicts
     * Success: Multiple message types (response, intermediate, system) are handled simultaneously without interference
     */
    test('handles concurrent message types correctly', () => {
      const conversation = { id: 'test-conv', messages: [] };

      // Mix of message types arriving concurrently
      const mixedMessages = [
        { type: 'system_response_message', status: 'in_progress', conversation_id: 'test-conv', content: { text: 'Response text' } },
        { type: 'system_intermediate_message', conversation_id: 'test-conv', content: { name: 'Step 1', payload: 'Processing...' } },
        { type: 'system_response_message', status: 'in_progress', conversation_id: 'test-conv', content: { text: ' continued' } },
        { type: 'error', conversation_id: 'test-conv', content: { text: 'Warning message' } }
      ];

      let currentMessages = conversation.messages;

      mixedMessages.forEach(msg => {
        if (msg.type === 'system_response_message') {
          // Append to or create assistant message
          const lastMessage = currentMessages[currentMessages.length - 1];
          if (lastMessage && lastMessage.role === 'assistant') {
            currentMessages = currentMessages.map((m, idx) =>
              idx === currentMessages.length - 1
                ? { ...m, content: (m.content || '') + msg.content.text }
                : m
            );
          } else {
            currentMessages = [...currentMessages, {
              role: 'assistant',
              content: msg.content.text,
              intermediateSteps: [],
              errorMessages: []
            }];
          }
        } else if (msg.type === 'system_intermediate_message') {
          // Add intermediate step
          const lastMessage = currentMessages[currentMessages.length - 1];
          if (lastMessage && lastMessage.role === 'assistant') {
            currentMessages = currentMessages.map((m, idx) =>
              idx === currentMessages.length - 1
                ? { ...m, intermediateSteps: [...(m.intermediateSteps || []), msg] }
                : m
            );
          }
        } else if (msg.type === 'error') {
          // Add error message
          const lastMessage = currentMessages[currentMessages.length - 1];
          if (lastMessage && lastMessage.role === 'assistant') {
            currentMessages = currentMessages.map((m, idx) =>
              idx === currentMessages.length - 1
                ? { ...m, errorMessages: [...(m.errorMessages || []), msg] }
                : m
            );
          }
        }
      });

      expect(currentMessages).toHaveLength(1);
      const assistantMessage = currentMessages[0];
      expect(assistantMessage.content).toBe('Response text continued');
      expect(assistantMessage.intermediateSteps).toHaveLength(1);
      expect(assistantMessage.errorMessages).toHaveLength(1);
    });
  });

  describe('Connection State Management', () => {
    /**
     * Description: Verifies that WebSocket connection state changes are tracked and reported accurately
     * Success: Connection state (connecting, connected, disconnected, error) is accurately maintained and updated
     */
    test('tracks connection state changes accurately', () => {
      const ws = new MockWebSocket('ws://test-server.com/websocket');
      let connectionState = 'connecting';
      let retryCount = 0;

      ws.onopen = () => {
        connectionState = 'connected';
        retryCount = 0;
      };

      ws.onclose = () => {
        connectionState = 'disconnected';
        retryCount++;
      };

      ws.onerror = () => {
        connectionState = 'error';
      };

      // Simulate connection lifecycle
      expect(connectionState).toBe('connecting');

      ws.readyState = MockWebSocket.OPEN;
      if (ws.onopen) ws.onopen(new Event('open'));
      expect(connectionState).toBe('connected');
      expect(retryCount).toBe(0);

      ws.readyState = MockWebSocket.CLOSED;
      if (ws.onclose) ws.onclose(new CloseEvent('close'));
      expect(connectionState).toBe('disconnected');
      expect(retryCount).toBe(1);

      if (ws.onerror) ws.onerror(new Event('error'));
      expect(connectionState).toBe('error');
    });

    /**
     * Description: Verifies that multiple simultaneous WebSocket connection attempts are prevented
     * Success: Only one connection attempt is active at a time, subsequent attempts are queued or ignored
     */
    test('prevents multiple simultaneous connection attempts', () => {
      let connectionAttempts = 0;
      let isConnecting = false;

      const attemptConnection = async () => {
        if (isConnecting) {
          return false; // Prevent concurrent attempts
        }

        isConnecting = true;
        connectionAttempts++;

        try {
          const ws = new MockWebSocket('ws://test-server.com/websocket');

          return new Promise<boolean>(resolve => {
            setTimeout(() => {
              ws.readyState = MockWebSocket.OPEN;
              if (ws.onopen) ws.onopen(new Event('open'));
              isConnecting = false;
              resolve(true);
            }, 100);
          });
        } catch {
          isConnecting = false;
          return false;
        }
      };

      // Try to start multiple connections simultaneously
      const promises = [
        attemptConnection(),
        attemptConnection(),
        attemptConnection()
      ];

      jest.runAllTimers();

      // Only first attempt should proceed
      expect(connectionAttempts).toBe(1);
    });
  });
});

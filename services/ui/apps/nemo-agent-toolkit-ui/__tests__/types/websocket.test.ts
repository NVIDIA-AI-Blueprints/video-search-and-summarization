/**
 * Unit tests for WebSocket type guards and utility functions
 */

import {
  isSystemResponseMessage,
  isSystemResponseInProgress,
  isSystemResponseComplete,
  isSystemIntermediateMessage,
  isSystemInteractionMessage,
  isErrorMessage,
  isOAuthConsentMessage,
  validateWebSocketMessage,
  validateConversationId,
  validateWebSocketMessageWithConversationId,
  extractOAuthUrl,
  shouldAppendResponseContent,
  SystemResponseMessage,
  SystemIntermediateMessage,
  SystemInteractionMessage,
  ErrorMessage,
} from '@/types/websocket';

describe('WebSocket Type Guards', () => {
  describe('isSystemResponseMessage', () => {
    it('returns true for valid system response message', () => {
      const message = {
        type: 'system_response_message',
        status: 'in_progress',
        content: { text: 'Hello' },
      };

      expect(isSystemResponseMessage(message)).toBe(true);
    });

    it('returns false for other message types', () => {
      const message = {
        type: 'system_intermediate_message',
        content: { payload: 'data' },
      };

      expect(isSystemResponseMessage(message)).toBe(false);
    });

    it('returns false for null/undefined', () => {
      expect(isSystemResponseMessage(null)).toBe(false);
      expect(isSystemResponseMessage(undefined)).toBe(false);
    });
  });

  describe('isSystemResponseInProgress', () => {
    it('returns true for in_progress system response', () => {
      const message: SystemResponseMessage = {
        type: 'system_response_message',
        status: 'in_progress',
        content: { text: 'Hello' },
      };

      expect(isSystemResponseInProgress(message)).toBe(true);
    });

    it('returns false for complete system response', () => {
      const message: SystemResponseMessage = {
        type: 'system_response_message',
        status: 'complete',
        content: { text: 'Hello' },
      };

      expect(isSystemResponseInProgress(message)).toBe(false);
    });

    it('returns false for non-system response messages', () => {
      const message = {
        type: 'error',
        content: { text: 'Error' },
      };

      expect(isSystemResponseInProgress(message)).toBe(false);
    });
  });

  describe('isSystemResponseComplete', () => {
    it('returns true for complete system response', () => {
      const message: SystemResponseMessage = {
        type: 'system_response_message',
        status: 'complete',
      };

      expect(isSystemResponseComplete(message)).toBe(true);
    });

    it('returns false for in_progress system response', () => {
      const message: SystemResponseMessage = {
        type: 'system_response_message',
        status: 'in_progress',
        content: { text: 'Hello' },
      };

      expect(isSystemResponseComplete(message)).toBe(false);
    });
  });

  describe('isSystemIntermediateMessage', () => {
    it('returns true for intermediate messages', () => {
      const message: SystemIntermediateMessage = {
        type: 'system_intermediate_message',
        content: { name: 'step', payload: 'data' },
      };

      expect(isSystemIntermediateMessage(message)).toBe(true);
    });

    it('returns false for other message types', () => {
      const message = {
        type: 'system_response_message',
        status: 'complete',
      };

      expect(isSystemIntermediateMessage(message)).toBe(false);
    });
  });

  describe('isSystemInteractionMessage', () => {
    it('returns true for interaction messages', () => {
      const message: SystemInteractionMessage = {
        type: 'system_interaction_message',
        content: { input_type: 'oauth_consent' },
      };

      expect(isSystemInteractionMessage(message)).toBe(true);
    });

    it('returns false for other message types', () => {
      const message = {
        type: 'error',
        content: { text: 'Error' },
      };

      expect(isSystemInteractionMessage(message)).toBe(false);
    });
  });

  describe('isErrorMessage', () => {
    it('returns true for error messages', () => {
      const message: ErrorMessage = {
        type: 'error',
        content: { text: 'Something went wrong' },
      };

      expect(isErrorMessage(message)).toBe(true);
    });

    it('returns false for other message types', () => {
      const message = {
        type: 'system_response_message',
        status: 'complete',
      };

      expect(isErrorMessage(message)).toBe(false);
    });
  });

  describe('isOAuthConsentMessage', () => {
    it('returns true for OAuth consent interaction messages', () => {
      const message: SystemInteractionMessage = {
        type: 'system_interaction_message',
        content: {
          input_type: 'oauth_consent',
          oauth_url: 'https://oauth.example.com',
        },
      };

      expect(isOAuthConsentMessage(message)).toBe(true);
    });

    it('returns false for non-OAuth interaction messages', () => {
      const message: SystemInteractionMessage = {
        type: 'system_interaction_message',
        content: { input_type: 'user_input' },
      };

      expect(isOAuthConsentMessage(message)).toBe(false);
    });

    it('returns false for non-interaction messages', () => {
      const message = {
        type: 'error',
        content: { text: 'Error' },
      };

      expect(isOAuthConsentMessage(message)).toBe(false);
    });
  });

  describe('validateWebSocketMessage', () => {
    it('validates system response messages', () => {
      const message = {
        type: 'system_response_message',
        status: 'in_progress',
        content: { text: 'Hello' },
      };

      expect(validateWebSocketMessage(message)).toBe(true);
    });

    it('validates intermediate messages', () => {
      const message = {
        type: 'system_intermediate_message',
        content: { payload: 'data' },
      };

      expect(validateWebSocketMessage(message)).toBe(true);
    });

    it('validates interaction messages', () => {
      const message = {
        type: 'system_interaction_message',
        content: { input_type: 'oauth_consent' },
      };

      expect(validateWebSocketMessage(message)).toBe(true);
    });

    it('validates error messages', () => {
      const message = {
        type: 'error',
        content: { text: 'Error occurred' },
      };

      expect(validateWebSocketMessage(message)).toBe(true);
    });

    it('rejects invalid message types', () => {
      const message = {
        type: 'invalid_type',
        content: { text: 'Hello' },
      };

      expect(validateWebSocketMessage(message)).toBe(false);
    });

    it('rejects messages without type', () => {
      const message = {
        content: { text: 'Hello' },
      };

      expect(validateWebSocketMessage(message)).toBe(false);
    });

    it('rejects null/undefined messages', () => {
      expect(validateWebSocketMessage(null)).toBe(false);
      expect(validateWebSocketMessage(undefined)).toBe(false);
    });

    it('rejects non-object messages', () => {
      expect(validateWebSocketMessage('string')).toBe(false);
      expect(validateWebSocketMessage(123)).toBe(false);
    });
  });

  describe('extractOAuthUrl', () => {
    it('extracts oauth_url from OAuth consent message', () => {
      const message: SystemInteractionMessage = {
        type: 'system_interaction_message',
        content: {
          input_type: 'oauth_consent',
          oauth_url: 'https://oauth.example.com/auth',
        },
      };

      expect(extractOAuthUrl(message)).toBe('https://oauth.example.com/auth');
    });

    it('extracts redirect_url when oauth_url is not available', () => {
      const message: SystemInteractionMessage = {
        type: 'system_interaction_message',
        content: {
          input_type: 'oauth_consent',
          redirect_url: 'https://redirect.example.com',
        },
      };

      expect(extractOAuthUrl(message)).toBe('https://redirect.example.com');
    });

    it('extracts text when neither oauth_url nor redirect_url is available', () => {
      const message: SystemInteractionMessage = {
        type: 'system_interaction_message',
        content: {
          input_type: 'oauth_consent',
          text: 'https://fallback.example.com',
        },
      };

      expect(extractOAuthUrl(message)).toBe('https://fallback.example.com');
    });

    it('returns null for non-OAuth consent messages', () => {
      const message: SystemInteractionMessage = {
        type: 'system_interaction_message',
        content: { input_type: 'user_input' },
      };

      expect(extractOAuthUrl(message)).toBe(null);
    });

    it('returns null when no URLs are available', () => {
      const message: SystemInteractionMessage = {
        type: 'system_interaction_message',
        content: { input_type: 'oauth_consent' },
      };

      expect(extractOAuthUrl(message)).toBe(null);
    });
  });

  describe('shouldAppendResponseContent', () => {
    it('returns true for in_progress system response with text', () => {
      const message: SystemResponseMessage = {
        type: 'system_response_message',
        status: 'in_progress',
        content: { text: 'Hello world' },
      };

      expect(shouldAppendResponseContent(message)).toBe(true);
    });

    it('returns false for complete system response', () => {
      const message: SystemResponseMessage = {
        type: 'system_response_message',
        status: 'complete',
        content: { text: 'Hello world' },
      };

      expect(shouldAppendResponseContent(message)).toBe(false);
    });

    it('returns false for system response without text', () => {
      const message: SystemResponseMessage = {
        type: 'system_response_message',
        status: 'in_progress',
        content: {},
      };

      expect(shouldAppendResponseContent(message)).toBe(false);
    });

    it('returns false for system response with empty text', () => {
      const message: SystemResponseMessage = {
        type: 'system_response_message',
        status: 'in_progress',
        content: { text: '' },
      };

      expect(shouldAppendResponseContent(message)).toBe(false);
    });

    it('returns false for non-system response messages', () => {
      const message: ErrorMessage = {
        type: 'error',
        content: { text: 'Error occurred' },
      };

      expect(shouldAppendResponseContent(message)).toBe(false);
    });
  });

  describe('validateConversationId', () => {
    it('returns true for valid conversation ID', () => {
      const message = {
        conversation_id: 'valid-conversation-123',
        type: 'system_response_message',
      };

      expect(validateConversationId(message)).toBe(true);
    });

    it('returns false for null message', () => {
      expect(validateConversationId(null)).toBe(false);
    });

    it('returns false for undefined message', () => {
      expect(validateConversationId(undefined)).toBe(false);
    });

    it('returns false for non-object message', () => {
      expect(validateConversationId('string')).toBe(false);
      expect(validateConversationId(123)).toBe(false);
      expect(validateConversationId(true)).toBe(false);
    });

    it('returns false for missing conversation_id', () => {
      const message = {
        type: 'system_response_message',
        status: 'in_progress',
      };

      expect(validateConversationId(message)).toBe(false);
    });

    it('returns false for non-string conversation_id', () => {
      const message = {
        conversation_id: 123,
        type: 'system_response_message',
      };

      expect(validateConversationId(message)).toBe(false);
    });

    it('returns false for empty string conversation_id', () => {
      const message = {
        conversation_id: '',
        type: 'system_response_message',
      };

      expect(validateConversationId(message)).toBe(false);
    });

    it('returns false for whitespace-only conversation_id', () => {
      const message = {
        conversation_id: '   \n\t  ',
        type: 'system_response_message',
      };

      expect(validateConversationId(message)).toBe(false);
    });

    it('returns true for conversation_id with whitespace that has content', () => {
      const message = {
        conversation_id: '  valid-id  ',
        type: 'system_response_message',
      };

      expect(validateConversationId(message)).toBe(true);
    });
  });

  describe('validateWebSocketMessageWithConversationId', () => {
    const validMessage = {
      type: 'system_response_message',
      conversation_id: 'valid-conversation-123',
      status: 'in_progress',
      content: { text: 'Hello' },
    };

    it('returns true for valid message with conversation ID', () => {
      expect(validateWebSocketMessageWithConversationId(validMessage)).toBe(true);
    });

    it('throws error for invalid message structure', () => {
      const invalidMessage = {
        type: 'invalid_type',
        conversation_id: 'valid-conversation-123',
      };

      expect(() => validateWebSocketMessageWithConversationId(invalidMessage))
        .toThrow('Invalid WebSocket message structure');
    });

    it('throws error for null message', () => {
      expect(() => validateWebSocketMessageWithConversationId(null))
        .toThrow('Invalid WebSocket message structure');
    });

    it('throws error for undefined message', () => {
      expect(() => validateWebSocketMessageWithConversationId(undefined))
        .toThrow('Invalid WebSocket message structure');
    });

    it('throws error for missing conversation_id', () => {
      const messageWithoutConversationId = {
        type: 'system_response_message',
        status: 'in_progress',
        content: { text: 'Hello' },
      };

      expect(() => validateWebSocketMessageWithConversationId(messageWithoutConversationId))
        .toThrow('WebSocket message missing required conversation_id');
    });

    it('throws error for empty conversation_id', () => {
      const messageWithEmptyConversationId = {
        type: 'system_response_message',
        conversation_id: '',
        status: 'in_progress',
        content: { text: 'Hello' },
      };

      expect(() => validateWebSocketMessageWithConversationId(messageWithEmptyConversationId))
        .toThrow('WebSocket message missing required conversation_id');
    });

    it('throws error for whitespace-only conversation_id', () => {
      const messageWithWhitespaceConversationId = {
        type: 'system_response_message',
        conversation_id: '   \n\t  ',
        status: 'in_progress',
        content: { text: 'Hello' },
      };

      expect(() => validateWebSocketMessageWithConversationId(messageWithWhitespaceConversationId))
        .toThrow('WebSocket message missing required conversation_id');
    });

    it('error message includes message type and conversation_id for debugging', () => {
      const messageWithoutConversationId = {
        type: 'system_intermediate_message',
        status: 'in_progress',
        content: { name: 'Step 1' },
      };

      try {
        validateWebSocketMessageWithConversationId(messageWithoutConversationId);
        fail('Expected error to be thrown');
      } catch (error: any) {
        expect(error.message).toContain('system_intermediate_message');
        expect(error.message).toContain('conversation_id');
      }
    });

    it('error message includes full message JSON for debugging', () => {
      const invalidMessage = {
        type: 'invalid_type',
        some_field: 'some_value',
      };

      try {
        validateWebSocketMessageWithConversationId(invalidMessage);
        fail('Expected error to be thrown');
      } catch (error: any) {
        expect(error.message).toContain(JSON.stringify(invalidMessage));
      }
    });

    it('validates all supported message types with conversation_id', () => {
      const messageTypes = [
        'system_response_message',
        'system_intermediate_message',
        'system_interaction_message',
        'error'
      ];

      messageTypes.forEach(type => {
        const message = {
          type,
          conversation_id: 'valid-conversation-123',
          status: 'in_progress',
          content: { text: 'Test' },
        };

        expect(validateWebSocketMessageWithConversationId(message)).toBe(true);
      });
    });
  });
});
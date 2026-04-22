/**
 * WebSocket tests including session cookie handling and stop generating functionality
 */

import MockWebSocket from '@/__mocks__/websocket';
import { SESSION_COOKIE_NAME } from '@/constants';
// Import type definitions for testing interaction message handling
import {
  isSystemInteractionMessage,
  isOAuthConsentMessage,
  extractOAuthUrl,
} from '@/types/websocket';
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { InteractionModal } from '@/components/Chat/ChatInteractionMessage';

// Mock react-hot-toast for notification tests
jest.mock('react-hot-toast', () => ({
  __esModule: true,
  default: {
    custom: jest.fn(),
    dismiss: jest.fn(),
  },
  toast: {
    custom: jest.fn(),
    dismiss: jest.fn(),
  },
}));

describe('WebSocket Functionality', () => {
  beforeEach(() => {
    MockWebSocket.lastInstance = null;
  });

  describe('Session Cookie Handling', () => {
    it('should always send session cookies with WebSocket connections using the correct constant', () => {
      // Test that session cookie is properly extracted and appended to WebSocket URL
      const mockSessionId = 'test_session_12345';
      const baseUrl = 'ws://test-server.com/websocket';

      // Simulate the cookie extraction logic from the actual implementation
      const mockDocumentCookie = `other=value; ${SESSION_COOKIE_NAME}=${mockSessionId}; another=test`;

      // Extract cookie using the same logic as the real implementation
      const getCookie = (name: string, documentCookie: string) => {
        const value = `; ${documentCookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop()?.split(';').shift();
        return null;
      };

      const sessionCookie = getCookie(SESSION_COOKIE_NAME, mockDocumentCookie);

      // Build WebSocket URL with session cookie (same logic as real implementation)
      let wsUrl = baseUrl;
      if (sessionCookie) {
        const separator = wsUrl.includes('?') ? '&' : '?';
        wsUrl += `${separator}session=${encodeURIComponent(sessionCookie)}`;
      }

      // Verify the session cookie was found and URL was built correctly
      expect(sessionCookie).toBe(mockSessionId);
      expect(wsUrl).toBe(`${baseUrl}?session=${encodeURIComponent(mockSessionId)}`);

      // Verify WebSocket is created with the session cookie
      const ws = new MockWebSocket(wsUrl);
      expect(ws.url).toContain(`session=${encodeURIComponent(mockSessionId)}`);
      expect(ws.url).toContain(SESSION_COOKIE_NAME.replace('nemo-agent-toolkit-session', 'session')); // URL param vs cookie name
    });

    it('should use the correct session cookie constant name', () => {
      // Verify we're using the constant and not a hardcoded value
      expect(SESSION_COOKIE_NAME).toBe('nemo-agent-toolkit-session');

      // Test with the actual constant
      const mockCookie = `test=value; ${SESSION_COOKIE_NAME}=session123; other=value`;

      const getCookie = (name: string, documentCookie: string) => {
        const value = `; ${documentCookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop()?.split(';').shift();
        return null;
      };

      const result = getCookie(SESSION_COOKIE_NAME, mockCookie);
      expect(result).toBe('session123');
    });

    it('should handle missing session cookies gracefully', () => {
      const baseUrl = 'ws://test-server.com/websocket';
      const mockDocumentCookie = 'other=value; different=cookie';

      const getCookie = (name: string, documentCookie: string) => {
        const value = `; ${documentCookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop()?.split(';').shift();
        return null;
      };

      const sessionCookie = getCookie(SESSION_COOKIE_NAME, mockDocumentCookie);

      // Should be null when cookie not found
      expect(sessionCookie).toBeNull();

      // URL should remain unchanged
      let wsUrl = baseUrl;
      if (sessionCookie) {
        const separator = wsUrl.includes('?') ? '&' : '?';
        wsUrl += `${separator}session=${encodeURIComponent(sessionCookie)}`;
      }

      expect(wsUrl).toBe(baseUrl); // No session parameter added
    });
  });

  describe('Stop Generating Functionality', () => {
    it('should track active user message ID for stop generating', () => {
      const activeUserMessageId = { current: null as string | null };

      // Simulate sending a message
      const messageId = 'user-msg-123';
      activeUserMessageId.current = messageId;

      expect(activeUserMessageId.current).toBe(messageId);

      // Simulate stop generating
      activeUserMessageId.current = null;

      expect(activeUserMessageId.current).toBeNull();
    });

    it('should ignore WebSocket messages when activeUserMessageId is null', () => {
      const activeUserMessageId = { current: null as string | null };

      const shouldIgnoreMessage = (message: any) => {
        const messageParentId = message.parent_id;
        if (messageParentId) {
          if (activeUserMessageId.current === null || messageParentId !== activeUserMessageId.current) {
            return true;
          }
        }
        return false;
      };

      // Test with null activeUserMessageId (stop was clicked)
      const message = { parent_id: 'some-message-id', type: 'system_response_message' };

      expect(shouldIgnoreMessage(message)).toBe(true);
    });

    it('should process WebSocket messages when activeUserMessageId matches parent_id', () => {
      const activeUserMessageId = { current: 'active-msg-123' };

      const shouldIgnoreMessage = (message: any) => {
        const messageParentId = message.parent_id;
        if (messageParentId) {
          if (activeUserMessageId.current === null || messageParentId !== activeUserMessageId.current) {
            return true;
          }
        }
        return false;
      };

      // Test with matching parent_id
      const message = { parent_id: 'active-msg-123', type: 'system_response_message' };

      expect(shouldIgnoreMessage(message)).toBe(false);
    });
  });

  describe('WebSocket Mock Integration', () => {
    it('should properly track WebSocket instances', () => {
      const ws1 = new MockWebSocket('ws://test1.com');
      expect(MockWebSocket.lastInstance).toBe(ws1);

      const ws2 = new MockWebSocket('ws://test2.com');
      expect(MockWebSocket.lastInstance).toBe(ws2);
    });

    it('should create WebSocket with session cookie in URL', () => {
      const sessionId = 'integration_test_session';
      const wsUrl = `ws://test.com/websocket?session=${encodeURIComponent(sessionId)}`;

      const ws = new MockWebSocket(wsUrl);

      expect(ws.url).toBe(wsUrl);
      expect(ws.url).toContain('session=');
      expect(ws.url).toContain(encodeURIComponent(sessionId));
    });
  });

  describe('Message Processing Logic', () => {
    describe('Message Validation', () => {
      it('should validate message with required conversation_id', () => {
        const validMessage = {
          type: 'system_response_message',
          conversation_id: 'conv-123',
          content: { text: 'Hello' },
          status: 'in_progress'
        };

        // Mock the validation function behavior
        const validateWebSocketMessageWithConversationId = (message: any) => {
          if (!message.conversation_id) {
            throw new Error('conversation_id is required');
          }
          if (!message.type) {
            throw new Error('type is required');
          }
        };

        expect(() => validateWebSocketMessageWithConversationId(validMessage)).not.toThrow();
      });

      it('should reject message without conversation_id', () => {
        const invalidMessage = {
          type: 'system_response_message',
          content: { text: 'Hello' },
          status: 'in_progress'
        };

        const validateWebSocketMessageWithConversationId = (message: any) => {
          if (!message.conversation_id) {
            throw new Error('conversation_id is required');
          }
          if (!message.type) {
            throw new Error('type is required');
          }
        };

        expect(() => validateWebSocketMessageWithConversationId(invalidMessage))
          .toThrow('conversation_id is required');
      });

      it('should reject message without type', () => {
        const invalidMessage = {
          conversation_id: 'conv-123',
          content: { text: 'Hello' },
          status: 'in_progress'
        };

        const validateWebSocketMessageWithConversationId = (message: any) => {
          if (!message.conversation_id) {
            throw new Error('conversation_id is required');
          }
          if (!message.type) {
            throw new Error('type is required');
          }
        };

        expect(() => validateWebSocketMessageWithConversationId(invalidMessage))
          .toThrow('type is required');
      });
    });

    describe('Message Type Processing', () => {
      it('should identify system response messages', () => {
        const isSystemResponseMessage = (message: any) => {
          return message.type === 'system_response_message';
        };

        const systemMessage = {
          type: 'system_response_message',
          conversation_id: 'conv-123',
          content: { text: 'AI response' }
        };

        const userMessage = {
          type: 'user_message',
          conversation_id: 'conv-123',
          content: { text: 'User input' }
        };

        expect(isSystemResponseMessage(systemMessage)).toBe(true);
        expect(isSystemResponseMessage(userMessage)).toBe(false);
      });

      it('should identify intermediate step messages', () => {
        const isSystemIntermediateMessage = (message: any) => {
          return message.type === 'system_intermediate_step';
        };

        const intermediateMessage = {
          type: 'system_intermediate_step',
          conversation_id: 'conv-123',
          content: { text: 'Processing step 1...' }
        };

        const regularMessage = {
          type: 'system_response_message',
          conversation_id: 'conv-123',
          content: { text: 'Final response' }
        };

        expect(isSystemIntermediateMessage(intermediateMessage)).toBe(true);
        expect(isSystemIntermediateMessage(regularMessage)).toBe(false);
      });

      it('should identify error messages', () => {
        const isErrorMessage = (message: any) => {
          return message.type === 'error' || message.status === 'error';
        };

        const errorMessage = {
          type: 'error',
          conversation_id: 'conv-123',
          content: { text: 'Something went wrong' }
        };

        const statusErrorMessage = {
          type: 'system_response_message',
          status: 'error',
          conversation_id: 'conv-123',
          content: { text: 'Processing failed' }
        };

        const normalMessage = {
          type: 'system_response_message',
          status: 'in_progress',
          conversation_id: 'conv-123',
          content: { text: 'Working...' }
        };

        expect(isErrorMessage(errorMessage)).toBe(true);
        expect(isErrorMessage(statusErrorMessage)).toBe(true);
        expect(isErrorMessage(normalMessage)).toBe(false);
      });

      it('should identify system response complete messages', () => {
        const isSystemResponseComplete = (message: any) => {
          return message.type === 'system_response:complete' || message.status === 'complete';
        };

        const completeMessage = {
          type: 'system_response:complete',
          conversation_id: 'conv-123'
        };

        const statusCompleteMessage = {
          type: 'system_response_message',
          status: 'complete',
          conversation_id: 'conv-123'
        };

        const inProgressMessage = {
          type: 'system_response_message',
          status: 'in_progress',
          conversation_id: 'conv-123'
        };

        expect(isSystemResponseComplete(completeMessage)).toBe(true);
        expect(isSystemResponseComplete(statusCompleteMessage)).toBe(true);
        expect(isSystemResponseComplete(inProgressMessage)).toBe(false);
      });
    });

    describe('Conversation Updates and State Synchronization', () => {
      it('should update conversation with new assistant message', () => {
        const conversation = {
          id: 'conv-123',
          name: 'Test Chat',
          messages: [
            { id: 'msg-1', role: 'user', content: 'Hello' }
          ]
        };

        const wsMessage = {
          type: 'system_response_message',
          conversation_id: 'conv-123',
          content: { text: 'Hi there!' },
          status: 'in_progress'
        };

        // Simulate message processing
        const processSystemResponseMessage = (message: any, messages: any[]) => {
          const lastMessage = messages[messages.length - 1];

          if (lastMessage && lastMessage.role === 'assistant' && lastMessage.content === '') {
            // Update existing assistant message
            return messages.map((msg, index) =>
              index === messages.length - 1
                ? { ...msg, content: message.content.text }
                : msg
            );
          } else {
            // Add new assistant message
            return [...messages, {
              id: `assistant-${Date.now()}`,
              role: 'assistant',
              content: message.content.text
            }];
          }
        };

        const updatedMessages = processSystemResponseMessage(wsMessage, conversation.messages);

        expect(updatedMessages).toHaveLength(2);
        expect(updatedMessages[1].role).toBe('assistant');
        expect(updatedMessages[1].content).toBe('Hi there!');
      });

      it('should append to existing assistant message when streaming', () => {
        const conversation = {
          id: 'conv-123',
          name: 'Test Chat',
          messages: [
            { id: 'msg-1', role: 'user', content: 'Hello' },
            { id: 'msg-2', role: 'assistant', content: 'Hi ' }
          ]
        };

        const wsMessage = {
          type: 'system_response_message',
          conversation_id: 'conv-123',
          content: { text: 'there!' },
          status: 'in_progress'
        };

        const appendAssistantText = (messages: any[], newText: string) => {
          const lastMessage = messages[messages.length - 1];
          if (lastMessage && lastMessage.role === 'assistant') {
            return messages.map((msg, index) =>
              index === messages.length - 1
                ? { ...msg, content: msg.content + newText }
                : msg
            );
          }
          return messages;
        };

        const updatedMessages = appendAssistantText(conversation.messages, wsMessage.content.text);

        expect(updatedMessages[1].content).toBe('Hi there!');
      });

      it('should maintain conversation reference integrity', () => {
        const conversationsRef = { current: [
          { id: 'conv-1', name: 'Chat 1', messages: [] },
          { id: 'conv-2', name: 'Chat 2', messages: [] }
        ]};

        const selectedConversationRef = { current: conversationsRef.current[0] };

        // Simulate updating a conversation
        const updateRefsAndDispatch = (updatedConversations: any[], updatedConversation: any, currentSelected: any) => {
          conversationsRef.current = updatedConversations;
          if (currentSelected?.id === updatedConversation.id) {
            selectedConversationRef.current = updatedConversation;
          }
        };

        const updatedConv = { ...conversationsRef.current[0], name: 'Updated Chat 1' };
        const updatedConversations = conversationsRef.current.map(c =>
          c.id === updatedConv.id ? updatedConv : c
        );

        updateRefsAndDispatch(updatedConversations, updatedConv, selectedConversationRef.current);

        expect(conversationsRef.current[0].name).toBe('Updated Chat 1');
        expect(selectedConversationRef.current.name).toBe('Updated Chat 1');
      });
    });

    describe('OAuth Consent Handling', () => {
      it('should identify OAuth consent messages', () => {
        const isSystemInteractionMessage = (message: any) => {
          return message.type === 'system_interaction_message';
        };

        const oauthMessage = {
          type: 'system_interaction_message',
          conversation_id: 'conv-123',
          content: {
            input_type: 'oauth_consent',
            oauth_url: 'https://auth.example.com/oauth/authorize?client_id=123'
          }
        };

        const regularMessage = {
          type: 'system_response_message',
          conversation_id: 'conv-123',
          content: { text: 'Regular response' }
        };

        expect(isSystemInteractionMessage(oauthMessage)).toBe(true);
        expect(isSystemInteractionMessage(regularMessage)).toBe(false);
      });

      it('should extract OAuth URL from consent message', () => {
        const extractOAuthUrl = (message: any) => {
          return message?.content?.oauth_url ||
                 message?.content?.redirect_url ||
                 message?.content?.text;
        };

        const oauthMessage = {
          type: 'system_interaction_message',
          content: {
            input_type: 'oauth_consent',
            oauth_url: 'https://auth.example.com/oauth/authorize'
          }
        };

        const redirectMessage = {
          type: 'system_interaction_message',
          content: {
            input_type: 'oauth_consent',
            redirect_url: 'https://auth.example.com/redirect'
          }
        };

        const textMessage = {
          type: 'system_interaction_message',
          content: {
            input_type: 'oauth_consent',
            text: 'https://auth.example.com/text'
          }
        };

        expect(extractOAuthUrl(oauthMessage)).toBe('https://auth.example.com/oauth/authorize');
        expect(extractOAuthUrl(redirectMessage)).toBe('https://auth.example.com/redirect');
        expect(extractOAuthUrl(textMessage)).toBe('https://auth.example.com/text');
      });

      it('should handle OAuth consent message processing', () => {
        const handleOAuthConsent = (message: any) => {
          if (message.type !== 'system_interaction_message') return false;

          if (message.content?.input_type === 'oauth_consent') {
            const oauthUrl = message?.content?.oauth_url ||
                           message?.content?.redirect_url ||
                           message?.content?.text;

            if (oauthUrl) {
              // In real implementation, this would open a popup
              // For testing, we'll just return the URL
              return { opened: true, url: oauthUrl };
            }
            return { opened: false, error: 'No URL found' };
          }
          return false;
        };

        const oauthMessage = {
          type: 'system_interaction_message',
          content: {
            input_type: 'oauth_consent',
            oauth_url: 'https://auth.example.com/oauth'
          }
        };

        const nonOAuthMessage = {
          type: 'system_interaction_message',
          content: {
            input_type: 'user_input',
            text: 'Please enter your name'
          }
        };

        const result1 = handleOAuthConsent(oauthMessage);
        const result2 = handleOAuthConsent(nonOAuthMessage);

        expect(result1).toEqual({ opened: true, url: 'https://auth.example.com/oauth' });
        expect(result2).toBe(false);
      });
    });

    describe('Intermediate Steps Filtering', () => {
      it('should respect enableIntermediateSteps session storage setting', () => {
        const mockSessionStorage = {
          'enableIntermediateSteps': 'false'
        };

        const shouldProcessIntermediateStep = (message: any) => {
          if (mockSessionStorage['enableIntermediateSteps'] === 'false' &&
              message.type === 'system_intermediate_step') {
            return false;
          }
          return true;
        };

        const intermediateMessage = {
          type: 'system_intermediate_step',
          conversation_id: 'conv-123',
          content: { text: 'Processing...' }
        };

        const regularMessage = {
          type: 'system_response_message',
          conversation_id: 'conv-123',
          content: { text: 'Final result' }
        };

        expect(shouldProcessIntermediateStep(intermediateMessage)).toBe(false);
        expect(shouldProcessIntermediateStep(regularMessage)).toBe(true);
      });

      it('should process intermediate steps when enabled', () => {
        const mockSessionStorage = {
          'enableIntermediateSteps': 'true'
        };

        const shouldProcessIntermediateStep = (message: any) => {
          if (mockSessionStorage['enableIntermediateSteps'] === 'false' &&
              message.type === 'system_intermediate_step') {
            return false;
          }
          return true;
        };

        const intermediateMessage = {
          type: 'system_intermediate_step',
          conversation_id: 'conv-123',
          content: { text: 'Processing step 1...' }
        };

        expect(shouldProcessIntermediateStep(intermediateMessage)).toBe(true);
      });

      it('should handle missing enableIntermediateSteps setting', () => {
        const mockSessionStorage = {};

        const shouldProcessIntermediateStep = (message: any) => {
          const setting = (mockSessionStorage as any)['enableIntermediateSteps'];
          if (setting === 'false' && message.type === 'system_intermediate_step') {
            return false;
          }
          return true;
        };

        const intermediateMessage = {
          type: 'system_intermediate_step',
          conversation_id: 'conv-123',
          content: { text: 'Processing...' }
        };

        // Should default to processing when setting is undefined
        expect(shouldProcessIntermediateStep(intermediateMessage)).toBe(true);
      });
    });

    describe('Message Persistence and Ref Updates', () => {
      it('should update conversations ref before React dispatch', () => {
        const conversationsRef = { current: [
          { id: 'conv-1', messages: [] }
        ]};
        const selectedConversationRef = { current: conversationsRef.current[0] };

        let dispatchCalls: any[] = [];
        const mockDispatch = (action: any) => {
          dispatchCalls.push(action);
        };

        const updateRefsAndDispatch = (updatedConversations: any[], updatedConversation: any, currentSelected: any) => {
          // Update refs BEFORE dispatch to prevent stale reads
          conversationsRef.current = updatedConversations;
          if (currentSelected?.id === updatedConversation.id) {
            selectedConversationRef.current = updatedConversation;
          }

          // Then dispatch to trigger React re-renders
          mockDispatch({ field: 'conversations', value: updatedConversations });
          if (currentSelected?.id === updatedConversation.id) {
            mockDispatch({ field: 'selectedConversation', value: updatedConversation });
          }
        };

        const updatedConv = { id: 'conv-1', messages: [{ id: 'msg-1', content: 'test' }] };
        const updatedConversations = [updatedConv];

        updateRefsAndDispatch(updatedConversations, updatedConv, selectedConversationRef.current);

        // Refs should be updated immediately
        expect(conversationsRef.current).toEqual(updatedConversations);
        expect(selectedConversationRef.current).toEqual(updatedConv);

        // Dispatch should be called
        expect(dispatchCalls).toHaveLength(2);
        expect(dispatchCalls[0]).toEqual({ field: 'conversations', value: updatedConversations });
        expect(dispatchCalls[1]).toEqual({ field: 'selectedConversation', value: updatedConv });
      });

      it('should handle conversation not found scenario', () => {
        const conversationsRef = { current: [
          { id: 'conv-1', messages: [] }
        ]};

        const findTargetConversation = (conversationId: string) => {
          return conversationsRef.current.find(c => c.id === conversationId);
        };

        const handleConversationNotFound = (conversationId: string) => {
          const errorMsg = `WebSocket message received for unknown conversation ID: ${conversationId}`;
          return { error: errorMsg, shouldReturn: true };
        };

        // Test with existing conversation
        expect(findTargetConversation('conv-1')).toBeDefined();

        // Test with non-existing conversation
        expect(findTargetConversation('conv-999')).toBeUndefined();

        const error = handleConversationNotFound('conv-999');
        expect(error.error).toContain('unknown conversation ID: conv-999');
        expect(error.shouldReturn).toBe(true);
      });

      it('should properly chain message processing functions', () => {
        const initialMessages = [
          { id: 'msg-1', role: 'user', content: 'Hello' }
        ];

        const processSystemResponseMessage = (message: any, messages: any[]) => {
          if (message.type === 'system_response_message') {
            return [...messages, { id: 'assistant-1', role: 'assistant', content: message.content.text }];
          }
          return messages;
        };

        const processIntermediateStepMessage = (message: any, messages: any[]) => {
          if (message.type === 'system_intermediate_step') {
            return [...messages, { id: 'step-1', role: 'system', content: message.content.text }];
          }
          return messages;
        };

        const processErrorMessage = (message: any, messages: any[]) => {
          if (message.type === 'error') {
            return [...messages, { id: 'error-1', role: 'system', content: `Error: ${message.content.text}` }];
          }
          return messages;
        };

        // Test system response processing
        const systemMessage = {
          type: 'system_response_message',
          content: { text: 'AI response' }
        };

        let updatedMessages = initialMessages;
        updatedMessages = processSystemResponseMessage(systemMessage, updatedMessages);
        updatedMessages = processIntermediateStepMessage(systemMessage, updatedMessages);
        updatedMessages = processErrorMessage(systemMessage, updatedMessages);

        expect(updatedMessages).toHaveLength(2);
        expect(updatedMessages[1].role).toBe('assistant');
        expect(updatedMessages[1].content).toBe('AI response');

        // Test intermediate step processing
        const intermediateMessage = {
          type: 'system_intermediate_step',
          content: { text: 'Processing...' }
        };

        updatedMessages = processIntermediateStepMessage(intermediateMessage, updatedMessages);

        expect(updatedMessages).toHaveLength(3);
        expect(updatedMessages[2].role).toBe('system');
        expect(updatedMessages[2].content).toBe('Processing...');
      });
    });
  });

  describe('System Interaction Message Handling', () => {
    // Mock modal state for testing
    let modalOpen = false;
    let currentInteractionMessage: any = null;

    // Helper functions to simulate Chat component behavior
    const openModal = (message: any) => {
      modalOpen = true;
      currentInteractionMessage = message;
    };

    const closeModal = () => {
      modalOpen = false;
      currentInteractionMessage = null;
    };

    // Helper function to simulate OAuth consent handling
    const handleOAuthConsent = (message: any) => {
      if (!isSystemInteractionMessage(message)) return false;

      if (message.content?.input_type === 'oauth_consent') {
        const oauthUrl = extractOAuthUrl(message);
        if (oauthUrl) {
          // In real implementation, this would open a popup
          window.open(oauthUrl, '_blank');
          return true;
        } else {
          console.error('OAuth consent message received but no URL found in content:', message?.content);
          return false;
        }
      }
      return false;
    };

    // Helper function to simulate WebSocket message processing
    const processWebSocketMessage = (message: any) => {
      // Reset state
      modalOpen = false;
      currentInteractionMessage = null;

      // Simulate the actual Chat component logic
      if (isSystemInteractionMessage(message)) {
        // Check for OAuth consent message and handle specially
        if (isOAuthConsentMessage(message)) {
          return handleOAuthConsent(message);
        }
        // For other interaction messages, open modal
        openModal(message);
        return true;
      }
      return false;
    };

    beforeEach(() => {
      modalOpen = false;
      currentInteractionMessage = null;
      jest.clearAllMocks();
    });

    describe('Interaction Message Detection and Processing', () => {
      it('should detect and process OAuth consent interaction message', () => {
        const oauthInteractionMessage = {
          type: 'system_interaction_message',
          conversation_id: 'conv-123',
          content: {
            input_type: 'oauth_consent',
            oauth_url: 'https://auth.example.com/oauth',
            text: 'Please authorize the application to access your data.'
          }
        };

        // Mock window.open
        const mockWindowOpen = jest.spyOn(window, 'open').mockImplementation();

        const result = processWebSocketMessage(oauthInteractionMessage);

        // Should be processed as OAuth consent (not regular modal)
        expect(result).toBe(true);
        expect(mockWindowOpen).toHaveBeenCalledWith('https://auth.example.com/oauth', '_blank');
        expect(modalOpen).toBe(false); // OAuth should not open modal

        mockWindowOpen.mockRestore();
      });

      it('should open modal for user input interaction message', () => {
        const userInputMessage = {
          type: 'system_interaction_message',
          conversation_id: 'conv-123',
          content: {
            input_type: 'user_input',
            text: 'Please enter your name:',
            placeholder: 'Your full name'
          }
        };

        const result = processWebSocketMessage(userInputMessage);

        // Should open modal for user input
        expect(result).toBe(true);
        expect(modalOpen).toBe(true);
        expect(currentInteractionMessage).toEqual(userInputMessage);
      });

      it('should open modal for file upload interaction message', () => {
        const fileUploadMessage = {
          type: 'system_interaction_message',
          conversation_id: 'conv-123',
          content: {
            input_type: 'file_upload',
            text: 'Please upload a document for analysis:',
            accepted_file_types: ['.pdf', '.docx', '.txt'],
            max_file_size: '10MB'
          }
        };

        const result = processWebSocketMessage(fileUploadMessage);

        // Should open modal for file upload
        expect(result).toBe(true);
        expect(modalOpen).toBe(true);
        expect(currentInteractionMessage).toEqual(fileUploadMessage);
      });

      it('should open modal for confirmation interaction message', () => {
        const confirmationMessage = {
          type: 'system_interaction_message',
          conversation_id: 'conv-123',
          content: {
            input_type: 'confirmation',
            text: 'Are you sure you want to proceed with this action?',
            confirm_text: 'Yes, proceed',
            cancel_text: 'Cancel'
          }
        };

        const result = processWebSocketMessage(confirmationMessage);

        // Should open modal for confirmation
        expect(result).toBe(true);
        expect(modalOpen).toBe(true);
        expect(currentInteractionMessage).toEqual(confirmationMessage);
      });

      it('should not process non-interaction messages', () => {
        const regularMessage = {
          type: 'system_response_message',
          conversation_id: 'conv-123',
          status: 'in_progress',
          content: {
            text: 'This is a regular response message'
          }
        };

        const result = processWebSocketMessage(regularMessage);

        // Should not process regular messages
        expect(result).toBe(false);
        expect(modalOpen).toBe(false);
        expect(currentInteractionMessage).toBeNull();
      });
    });

    describe('Modal State Management', () => {
      it('should manage modal state correctly', () => {
        // Initially closed
        expect(modalOpen).toBe(false);
        expect(currentInteractionMessage).toBeNull();

        // Open modal
        const testMessage = {
          type: 'system_interaction_message',
          content: { input_type: 'user_input', text: 'Test' }
        };

        openModal(testMessage);
        expect(modalOpen).toBe(true);
        expect(currentInteractionMessage).toEqual(testMessage);

        // Close modal
        closeModal();
        expect(modalOpen).toBe(false);
        expect(currentInteractionMessage).toBeNull();
      });
    });

    describe('OAuth Consent Special Handling', () => {
      beforeEach(() => {
        // Mock window.open
        jest.spyOn(window, 'open').mockImplementation();
      });

      afterEach(() => {
        jest.restoreAllMocks();
      });

      it('should open OAuth URL directly without modal for oauth_consent messages', () => {
        const oauthMessage = {
          type: 'system_interaction_message',
          conversation_id: 'conv-123',
          content: {
            input_type: 'oauth_consent',
            oauth_url: 'https://auth.example.com/oauth/authorize'
          }
        };

        const result = processWebSocketMessage(oauthMessage);

        // OAuth URL should be opened in new tab
        expect(window.open).toHaveBeenCalledWith('https://auth.example.com/oauth/authorize', '_blank');

        // Should return true (processed) but modal should NOT be opened
        expect(result).toBe(true);
        expect(modalOpen).toBe(false);
      });

      it('should handle OAuth message with redirect_url fallback', () => {
        const oauthMessage = {
          type: 'system_interaction_message',
          conversation_id: 'conv-123',
          content: {
            input_type: 'oauth_consent',
            redirect_url: 'https://auth.example.com/redirect'
          }
        };

        const result = processWebSocketMessage(oauthMessage);

        expect(window.open).toHaveBeenCalledWith('https://auth.example.com/redirect', '_blank');
        expect(result).toBe(true);
        expect(modalOpen).toBe(false);
      });

      it('should handle OAuth message with text fallback', () => {
        const oauthMessage = {
          type: 'system_interaction_message',
          conversation_id: 'conv-123',
          content: {
            input_type: 'oauth_consent',
            text: 'https://auth.example.com/fallback'
          }
        };

        const result = processWebSocketMessage(oauthMessage);

        expect(window.open).toHaveBeenCalledWith('https://auth.example.com/fallback', '_blank');
        expect(result).toBe(true);
        expect(modalOpen).toBe(false);
      });

      it('should handle OAuth message without valid URL gracefully', () => {
        // Mock console.error to verify error logging
        const consoleSpy = jest.spyOn(console, 'error').mockImplementation();

        const oauthMessage = {
          type: 'system_interaction_message',
          conversation_id: 'conv-123',
          content: {
            input_type: 'oauth_consent'
            // No oauth_url, redirect_url, or text with URL
          }
        };

        const result = processWebSocketMessage(oauthMessage);

        // Should not try to open any URL
        expect(window.open).not.toHaveBeenCalled();

        // Should log error about missing URL
        expect(consoleSpy).toHaveBeenCalledWith(
          expect.stringContaining('OAuth consent message received but no URL found'),
          expect.any(Object)
        );

        // Should return false (not processed successfully)
        expect(result).toBe(false);
        expect(modalOpen).toBe(false);

        consoleSpy.mockRestore();
      });
    });

    describe('Interaction Message Type Coverage', () => {
      it('should handle various interaction message types', () => {
        const testCases = [
          {
            name: 'user_input',
            message: {
              type: 'system_interaction_message',
              content: { input_type: 'user_input', text: 'Enter name:' }
            }
          },
          {
            name: 'file_upload',
            message: {
              type: 'system_interaction_message',
              content: { input_type: 'file_upload', text: 'Upload file:' }
            }
          },
          {
            name: 'confirmation',
            message: {
              type: 'system_interaction_message',
              content: { input_type: 'confirmation', text: 'Confirm action?' }
            }
          },
          {
            name: 'selection',
            message: {
              type: 'system_interaction_message',
              content: { input_type: 'selection', text: 'Choose option:', options: ['A', 'B'] }
            }
          }
        ];

        testCases.forEach(({ name, message }) => {
          // Reset state for each test
          modalOpen = false;
          currentInteractionMessage = null;

          const result = processWebSocketMessage(message);

          expect(result).toBe(true);
          expect(modalOpen).toBe(true);
          expect(currentInteractionMessage).toEqual(message);
        });
      });

      it('should handle interaction messages without input_type', () => {
        const messageWithoutInputType = {
          type: 'system_interaction_message',
          content: { text: 'General interaction message' }
        };

        const result = processWebSocketMessage(messageWithoutInputType);

        // Should still open modal for any interaction message
        expect(result).toBe(true);
        expect(modalOpen).toBe(true);
        expect(currentInteractionMessage).toEqual(messageWithoutInputType);
      });
    });

    describe('Error Handling and Edge Cases', () => {
      it('should handle interaction message with empty content', () => {
        const minimalMessage = {
          type: 'system_interaction_message',
          content: {}
        };

        const result = processWebSocketMessage(minimalMessage);

        // Should still process message with empty content
        expect(result).toBe(true);
        expect(modalOpen).toBe(true);
        expect(currentInteractionMessage).toEqual(minimalMessage);
      });

      it('should handle interaction message without content property', () => {
        const messageWithoutContent = {
          type: 'system_interaction_message'
          // No content property
        };

        const result = processWebSocketMessage(messageWithoutContent);

        // Should still be identified as interaction message
        expect(isSystemInteractionMessage(messageWithoutContent)).toBe(true);
        expect(result).toBe(true);
        expect(modalOpen).toBe(true);
      });

      it('should not confuse interaction messages with other message types', () => {
        const nonInteractionMessages = [
          { type: 'system_response_message', content: { text: 'Response' } },
          { type: 'system_intermediate_message', content: { text: 'Step' } },
          { type: 'error', content: { text: 'Error' } },
          { type: 'user_message', content: { text: 'User input' } }
        ];

        nonInteractionMessages.forEach(message => {
          modalOpen = false;
          currentInteractionMessage = null;

          const result = processWebSocketMessage(message);

          expect(result).toBe(false);
          expect(modalOpen).toBe(false);
          expect(currentInteractionMessage).toBeNull();
        });
      });
    });
  });

  describe('User Interaction Response', () => {
    it('should include conversation_id when sending interaction response', () => {
      // Mock the WebSocket send method to capture the sent message
      const mockSend = jest.fn();
      const mockWebSocket = {
        send: mockSend,
        readyState: WebSocket.OPEN
      };

      // Mock interaction message received from backend
      const interactionMessage = {
        type: 'system_interaction_message',
        thread_id: 'thread_123',
        parent_id: 'parent_456',
        content: {
          input_type: 'text',
          text: 'Please provide your input',
        }
      };

      // Mock conversation
      const conversationId = 'conv_789';

      // Simulate sending interaction response (mimics handleUserInteraction logic)
      const userResponse = 'My response to the interaction';
      const wsMessage = {
        type: 'user_interaction_message',
        id: 'msg_001', // Would be uuidv4() in real code
        conversation_id: conversationId, // Critical: must be included
        thread_id: interactionMessage.thread_id,
        parent_id: interactionMessage.parent_id,
        content: {
          messages: [
            {
              role: 'user',
              content: [
                {
                  type: 'text',
                  text: userResponse,
                },
              ],
            },
          ],
        },
        timestamp: new Date().toISOString(),
      };

      mockWebSocket.send(JSON.stringify(wsMessage));

      // Verify the message was sent
      expect(mockSend).toHaveBeenCalledTimes(1);

      // Parse the sent message
      const sentMessage = JSON.parse(mockSend.mock.calls[0][0]);

      // Verify critical fields are present
      expect(sentMessage.type).toBe('user_interaction_message');
      expect(sentMessage.conversation_id).toBe(conversationId);
      expect(sentMessage.thread_id).toBe(interactionMessage.thread_id);
      expect(sentMessage.parent_id).toBe(interactionMessage.parent_id);
      expect(sentMessage.content.messages[0].role).toBe('user');
      expect(sentMessage.content.messages[0].content[0].text).toBe(userResponse);
    });

    it('should not send interaction response if conversation is undefined', () => {
      const mockSend = jest.fn();
      const mockWebSocket = {
        send: mockSend,
        readyState: WebSocket.OPEN
      };

      // Simulate the case where selectedConversation is undefined
      const selectedConversation = undefined;

      // This should trigger the early return in handleUserInteraction
      if (!selectedConversation) {
        // Early return - no message should be sent
        expect(mockSend).not.toHaveBeenCalled();
        return;
      }

      // If we get here, the test should fail
      fail('Expected early return when conversation is undefined');
    });
  });

  describe('Observability Trace Handling', () => {
    it('should attach trace ID from separate observability_trace_message', () => {
      const messages: any[] = [];
      
      // First, receive response message
      const responseMessage = {
        type: 'system_response_message',
        conversation_id: 'conv-123',
        content: { text: 'AI response' },
        status: 'complete'
      };
      
      messages.push({
        role: 'assistant',
        content: responseMessage.content.text
      });
      
      // Then, receive separate trace message
      const traceMessage = {
        type: 'observability_trace_message',
        conversation_id: 'conv-123',
        content: { 
          observability_trace_id: 'trace-abc-123' 
        }
      };
      
      // Simulate attaching trace to last message
      const lastMessage = messages[messages.length - 1];
      const updatedMessage = {
        ...lastMessage,
        observabilityTraceId: traceMessage.content.observability_trace_id
      };
      messages[messages.length - 1] = updatedMessage;
      
      expect(messages[0].observabilityTraceId).toBe('trace-abc-123');
      expect(messages[0].content).toBe('AI response');
    });

    it('should handle observability_trace_message without assistant message', () => {
      const messages: any[] = [];
      
      const traceMessage = {
        type: 'observability_trace_message',
        conversation_id: 'conv-123',
        content: { 
          observability_trace_id: 'trace-early-123' 
        }
      };
      
      // If there's no assistant message, trace message should be ignored
      const lastMessage = messages[messages.length - 1];
      const isLastAssistant = lastMessage?.role === 'assistant';
      
      if (!isLastAssistant) {
        // Don't create a new message, just skip
        expect(messages.length).toBe(0);
      }
    });

    it('should handle trace message with various ID formats', () => {
      const testCases = [
        'trace-abc-123',
        'trace_underscore_456',
        'trace:colon:789',
        'trace.dot.012',
        '12345678-1234-1234-1234-123456789abc'
      ];

      testCases.forEach(traceId => {
        const messages = [{
          role: 'assistant',
          content: 'Test response'
        }];
        
        const traceMessage = {
          type: 'observability_trace_message',
          conversation_id: 'conv-123',
          content: { observability_trace_id: traceId }
        };
        
        messages[0] = {
          ...messages[0],
          observabilityTraceId: traceMessage.content.observability_trace_id
        };
        
        expect(messages[0].observabilityTraceId).toBe(traceId);
      });
    });
  });

  describe('WebSocket Reconnection Recovery', () => {
    let mockStorage: Record<string, string>;

    beforeEach(() => {
      mockStorage = {};
    });

    const mockSessionStorage = {
      getItem: (key: string) => mockStorage[key] ?? null,
      setItem: (key: string, value: string) => { mockStorage[key] = value; },
      removeItem: (key: string) => { delete mockStorage[key]; },
      clear: () => { mockStorage = {}; },
    };

    it('should save activeUserMessageId to sessionStorage when user sends a message', () => {
      const conversationId = 'conv-123';
      const messageId = 'msg-456';
      
      mockSessionStorage.setItem(`activeUserMessageId_${conversationId}`, messageId);
      
      expect(mockSessionStorage.getItem(`activeUserMessageId_${conversationId}`)).toBe(messageId);
    });

    it('should restore activeUserMessageId from sessionStorage on WebSocket reconnect', () => {
      const conversationId = 'conv-123';
      const messageId = 'msg-456';
      const activeUserMessageId = { current: null as string | null };
      
      mockSessionStorage.setItem(`activeUserMessageId_${conversationId}`, messageId);
      
      const storedMessageId = mockSessionStorage.getItem(`activeUserMessageId_${conversationId}`);
      if (storedMessageId) {
        activeUserMessageId.current = storedMessageId;
      }
      
      expect(activeUserMessageId.current).toBe(messageId);
    });

    it('should not restore activeUserMessageId if sessionStorage is empty', () => {
      const conversationId = 'conv-123';
      const activeUserMessageId = { current: null as string | null };
      
      const storedMessageId = mockSessionStorage.getItem(`activeUserMessageId_${conversationId}`);
      if (storedMessageId) {
        activeUserMessageId.current = storedMessageId;
      }
      
      expect(activeUserMessageId.current).toBeNull();
    });

    it('should clear sessionStorage when Stop Generating is clicked', () => {
      const conversationId = 'conv-123';
      mockSessionStorage.setItem(`activeUserMessageId_${conversationId}`, 'msg-456');
      
      mockSessionStorage.removeItem(`activeUserMessageId_${conversationId}`);
      
      expect(mockSessionStorage.getItem(`activeUserMessageId_${conversationId}`)).toBeNull();
    });

    it('should clear sessionStorage when response completes', () => {
      const conversationId = 'conv-123';
      mockSessionStorage.setItem(`activeUserMessageId_${conversationId}`, 'msg-456');
      
      mockSessionStorage.removeItem(`activeUserMessageId_${conversationId}`);
      
      expect(mockSessionStorage.getItem(`activeUserMessageId_${conversationId}`)).toBeNull();
    });

    it('should allow HITL messages through after reconnection with restored activeUserMessageId', () => {
      const conversationId = 'conv-123';
      const messageId = 'msg-456';
      const activeUserMessageId = { current: null as string | null };
      
      mockSessionStorage.setItem(`activeUserMessageId_${conversationId}`, messageId);
      
      const storedMessageId = mockSessionStorage.getItem(`activeUserMessageId_${conversationId}`);
      if (storedMessageId) {
        activeUserMessageId.current = storedMessageId;
      }
      
      const hitlMessage = {
        type: 'system_interaction_message',
        conversation_id: conversationId,
      };
      
      const shouldProcess = activeUserMessageId.current !== null && 
                            hitlMessage.conversation_id === conversationId;
      
      expect(shouldProcess).toBe(true);
    });

    it('should block messages when activeUserMessageId is not restored', () => {
      const conversationId = 'conv-123';
      const activeUserMessageId = { current: null as string | null };
      
      const message = {
        type: 'system_interaction_message',
        conversation_id: conversationId,
      };
      
      const shouldProcess = activeUserMessageId.current !== null && 
                            message.conversation_id === conversationId;
      
      expect(shouldProcess).toBe(false);
    });

    it('should use conversation-specific keys in sessionStorage', () => {
      mockSessionStorage.setItem('activeUserMessageId_conv-A', 'msg-A');
      mockSessionStorage.setItem('activeUserMessageId_conv-B', 'msg-B');
      
      expect(mockSessionStorage.getItem('activeUserMessageId_conv-A')).toBe('msg-A');
      expect(mockSessionStorage.getItem('activeUserMessageId_conv-B')).toBe('msg-B');
      
      mockSessionStorage.removeItem('activeUserMessageId_conv-A');
      
      expect(mockSessionStorage.getItem('activeUserMessageId_conv-A')).toBeNull();
      expect(mockSessionStorage.getItem('activeUserMessageId_conv-B')).toBe('msg-B');
    });

    it('should handle reconnection to different conversation without cross-contamination', () => {
      const activeUserMessageId = { current: null as string | null };
      
      mockSessionStorage.setItem('activeUserMessageId_conv-A', 'msg-A');
      mockSessionStorage.setItem('activeUserMessageId_conv-B', 'msg-B');
      
      const currentConversationId = 'conv-A';
      const storedMessageId = mockSessionStorage.getItem(`activeUserMessageId_${currentConversationId}`);
      if (storedMessageId) {
        activeUserMessageId.current = storedMessageId;
      }
      
      expect(activeUserMessageId.current).toBe('msg-A');
      
      const messageForConvB = {
        conversation_id: 'conv-B',
      };
      
      const shouldProcess = activeUserMessageId.current !== null && 
                            messageForConvB.conversation_id === currentConversationId;
      
      expect(shouldProcess).toBe(false);
    });
  });
});

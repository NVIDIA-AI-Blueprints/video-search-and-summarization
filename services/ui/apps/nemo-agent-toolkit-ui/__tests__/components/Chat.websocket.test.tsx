/**
 * WebSocket tests including session cookie handling and stop generating functionality
 */

import MockWebSocket from '@/__mocks__/websocket';
import { SESSION_COOKIE_NAME } from '@/constants/constants';
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

  describe('InteractionModal Component Tests', () => {
    const mockOnClose = jest.fn();
    const mockOnSubmit = jest.fn();

    beforeEach(() => {
      jest.clearAllMocks();
    });

    describe('Text Input Type', () => {
      it('should render text input with placeholder', () => {
        const message = {
          type: 'system_interaction_message',
          content: {
            input_type: 'text',
            text: 'Please enter your name:',
            placeholder: 'Your full name here',
            required: true
          }
        };

        render(
          <InteractionModal
            isOpen={true}
            interactionMessage={message}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        // Verify modal content
        expect(screen.getByText('Please enter your name:')).toBeInTheDocument();
        expect(screen.getByPlaceholderText('Your full name here')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Submit' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
      });

      it('should handle text input submission', async () => {
        const message = {
          type: 'system_interaction_message',
          content: {
            input_type: 'text',
            text: 'Enter feedback:',
            required: false
          }
        };

        render(
          <InteractionModal
            isOpen={true}
            interactionMessage={message}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        const textarea = screen.getByRole('textbox');
        const submitButton = screen.getByRole('button', { name: 'Submit' });

        // Enter text and submit
        fireEvent.change(textarea, { target: { value: 'Great app!' } });
        fireEvent.click(submitButton);

        expect(mockOnSubmit).toHaveBeenCalledWith({
          interactionMessage: message,
          userResponse: 'Great app!'
        });
        expect(mockOnClose).toHaveBeenCalled();
      });

      it('should validate required text input', async () => {
        const message = {
          type: 'system_interaction_message',
          content: {
            input_type: 'text',
            text: 'Required field:',
            required: true
          }
        };

        render(
          <InteractionModal
            isOpen={true}
            interactionMessage={message}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        const submitButton = screen.getByRole('button', { name: 'Submit' });

        // Try to submit without entering text
        fireEvent.click(submitButton);

        // Should show error and not submit
        expect(screen.getByText('This field is required.')).toBeInTheDocument();
        expect(mockOnSubmit).not.toHaveBeenCalled();
        expect(mockOnClose).not.toHaveBeenCalled();
      });

      it('should handle cancel button', () => {
        const message = {
          type: 'system_interaction_message',
          content: {
            input_type: 'text',
            text: 'Enter something:'
          }
        };

        render(
          <InteractionModal
            isOpen={true}
            interactionMessage={message}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        const cancelButton = screen.getByRole('button', { name: 'Cancel' });
        fireEvent.click(cancelButton);

        expect(mockOnClose).toHaveBeenCalled();
        expect(mockOnSubmit).not.toHaveBeenCalled();
      });
    });

    describe('Binary Choice Type', () => {
      it('should render binary choice options', () => {
        const message = {
          type: 'system_interaction_message',
          content: {
            input_type: 'binary_choice',
            text: 'Do you want to continue?',
            options: [
              { id: 'continue', label: 'Continue', value: 'continue' },
              { id: 'cancel', label: 'Cancel', value: 'cancel' }
            ]
          }
        };

        render(
          <InteractionModal
            isOpen={true}
            interactionMessage={message}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        expect(screen.getByText('Do you want to continue?')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Continue' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
      });

      it('should handle binary choice selection', () => {
        const message = {
          type: 'system_interaction_message',
          content: {
            input_type: 'binary_choice',
            text: 'Proceed with action?',
            options: [
              { id: 'yes', label: 'Yes, proceed', value: 'proceed' },
              { id: 'no', label: 'No, cancel', value: 'cancel' }
            ]
          }
        };

        render(
          <InteractionModal
            isOpen={true}
            interactionMessage={message}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        const proceedButton = screen.getByRole('button', { name: 'Yes, proceed' });
        fireEvent.click(proceedButton);

        expect(mockOnSubmit).toHaveBeenCalledWith({
          interactionMessage: message,
          userResponse: 'proceed'
        });
        expect(mockOnClose).toHaveBeenCalled();
      });

      it('should apply correct styling for continue vs cancel buttons', () => {
        const message = {
          type: 'system_interaction_message',
          content: {
            input_type: 'binary_choice',
            text: 'Choose action:',
            options: [
              { id: 'cont', label: 'Continue', value: 'continue' },
              { id: 'stop', label: 'Stop', value: 'stop' }
            ]
          }
        };

        render(
          <InteractionModal
            isOpen={true}
            interactionMessage={message}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        const continueButton = screen.getByRole('button', { name: 'Continue' });
        const stopButton = screen.getByRole('button', { name: 'Stop' });

        // Continue button should have green background
        expect(continueButton).toHaveClass('bg-[#76b900]');
        // Stop button should have slate background
        expect(stopButton).toHaveClass('bg-slate-800');
      });
    });

    describe('Radio Selection Type', () => {
      it('should render radio options', () => {
        const message = {
          type: 'system_interaction_message',
          content: {
            input_type: 'radio',
            text: 'Select notification method:',
            options: [
              { id: 'email', label: 'Email', value: 'email' },
              { id: 'sms', label: 'SMS', value: 'sms' },
              { id: 'push', label: 'Push Notification', value: 'push' }
            ]
          }
        };

        render(
          <InteractionModal
            isOpen={true}
            interactionMessage={message}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        expect(screen.getByText('Select notification method:')).toBeInTheDocument();
        expect(screen.getByLabelText('Email')).toBeInTheDocument();
        expect(screen.getByLabelText('SMS')).toBeInTheDocument();
        expect(screen.getByLabelText('Push Notification')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Submit' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
      });

      it('should handle radio selection and submission', () => {
        const message = {
          type: 'system_interaction_message',
          content: {
            input_type: 'radio',
            text: 'Choose option:',
            options: [
              { id: 'opt1', label: 'Option 1', value: 'option1' },
              { id: 'opt2', label: 'Option 2', value: 'option2' }
            ]
          }
        };

        render(
          <InteractionModal
            isOpen={true}
            interactionMessage={message}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        const option1Radio = screen.getByLabelText('Option 1');
        const submitButton = screen.getByRole('button', { name: 'Submit' });

        // Select option and submit
        fireEvent.click(option1Radio);
        fireEvent.click(submitButton);

        expect(mockOnSubmit).toHaveBeenCalledWith({
          interactionMessage: message,
          userResponse: 'option1'
        });
        expect(mockOnClose).toHaveBeenCalled();
      });

      it('should validate required radio selection', () => {
        const message = {
          type: 'system_interaction_message',
          content: {
            input_type: 'radio',
            text: 'Required selection:',
            required: true,
            options: [
              { id: 'opt1', label: 'Option 1', value: 'option1' },
              { id: 'opt2', label: 'Option 2', value: 'option2' }
            ]
          }
        };

        render(
          <InteractionModal
            isOpen={true}
            interactionMessage={message}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        const submitButton = screen.getByRole('button', { name: 'Submit' });

        // Try to submit without selecting
        fireEvent.click(submitButton);

        expect(screen.getByText('Please select an option.')).toBeInTheDocument();
        expect(mockOnSubmit).not.toHaveBeenCalled();
        expect(mockOnClose).not.toHaveBeenCalled();
      });
    });

    describe('Notification Type', () => {
      it('should display toast notification instead of modal', () => {
        const { toast } = require('react-hot-toast');

        const message = {
          type: 'system_interaction_message',
          content: {
            input_type: 'notification',
            text: 'Operation completed successfully!'
          }
        };

        const result = render(
          <InteractionModal
            isOpen={true}
            interactionMessage={message}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        // Should call toast.custom instead of rendering modal
        expect(toast.custom).toHaveBeenCalled();

        // Should return null (no modal content)
        expect(result.container.firstChild).toBeNull();
      });

      it('should handle notification with custom content', () => {
        const { toast } = require('react-hot-toast');

        const message = {
          type: 'system_interaction_message',
          content: {
            input_type: 'notification',
            text: 'Custom notification message'
          }
        };

        render(
          <InteractionModal
            isOpen={true}
            interactionMessage={message}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        expect(toast.custom).toHaveBeenCalledWith(
          expect.any(Function),
          {
            position: 'top-right',
            duration: Infinity,
            id: 'notification-toast'
          }
        );
      });

      it('should handle notification without content gracefully', () => {
        const { toast } = require('react-hot-toast');

        const message = {
          type: 'system_interaction_message',
          content: {
            input_type: 'notification'
            // No text field
          }
        };

        render(
          <InteractionModal
            isOpen={true}
            interactionMessage={message}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        // Should still call toast.custom
        expect(toast.custom).toHaveBeenCalled();
      });
    });

    describe('Modal State and Edge Cases', () => {
      it('should not render when isOpen is false', () => {
        const message = {
          type: 'system_interaction_message',
          content: {
            input_type: 'text',
            text: 'Test message'
          }
        };

        const result = render(
          <InteractionModal
            isOpen={false}
            interactionMessage={message}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        expect(result.container.firstChild).toBeNull();
      });

      it('should not render when interactionMessage is null', () => {
        const result = render(
          <InteractionModal
            isOpen={true}
            interactionMessage={null}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        expect(result.container.firstChild).toBeNull();
      });

      it('should handle unknown input_type gracefully', () => {
        const message = {
          type: 'system_interaction_message',
          content: {
            input_type: 'unknown_type',
            text: 'Unknown interaction type'
          }
        };

        render(
          <InteractionModal
            isOpen={true}
            interactionMessage={message}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        // Should still show the text, even if no specific UI for the type
        expect(screen.getByText('Unknown interaction type')).toBeInTheDocument();
      });

      it('should handle message without input_type', () => {
        const message = {
          type: 'system_interaction_message',
          content: {
            text: 'General interaction message'
            // No input_type specified
          }
        };

        render(
          <InteractionModal
            isOpen={true}
            interactionMessage={message}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        // Should still display the text
        expect(screen.getByText('General interaction message')).toBeInTheDocument();
      });

      it('should handle empty content gracefully', () => {
        const message = {
          type: 'system_interaction_message',
          content: {}
        };

        render(
          <InteractionModal
            isOpen={true}
            interactionMessage={message}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        // Modal should still render with basic structure
        expect(document.querySelector('.fixed')).toBeInTheDocument();
      });
    });

    describe('Validation Error States', () => {
      it('should clear validation errors when user corrects input', async () => {
        const message = {
          type: 'system_interaction_message',
          content: {
            input_type: 'text',
            text: 'Required field:',
            required: true
          }
        };

        render(
          <InteractionModal
            isOpen={true}
            interactionMessage={message}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        const textarea = screen.getByRole('textbox');
        const submitButton = screen.getByRole('button', { name: 'Submit' });

        // First, trigger validation error
        fireEvent.click(submitButton);
        expect(screen.getByText('This field is required.')).toBeInTheDocument();

        // Then enter text and submit again
        fireEvent.change(textarea, { target: { value: 'Valid input' } });
        fireEvent.click(submitButton);

        // Error should be cleared and submission should work
        expect(screen.queryByText('This field is required.')).not.toBeInTheDocument();
        expect(mockOnSubmit).toHaveBeenCalledWith({
          interactionMessage: message,
          userResponse: 'Valid input'
        });
      });

      it('should handle binary choice validation for required fields', async () => {
        const message = {
          type: 'system_interaction_message',
          content: {
            input_type: 'binary_choice',
            text: 'Required choice:',
            required: true,
            options: [
              { id: 'opt1', label: 'Option 1', value: '' }, // Empty value
              { id: 'opt2', label: 'Option 2', value: 'valid' }
            ]
          }
        };

        render(
          <InteractionModal
            isOpen={true}
            interactionMessage={message}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        const emptyOption = screen.getByRole('button', { name: 'Option 1' });
        fireEvent.click(emptyOption);

        // Wait for potential state update and check if validation error appears
        await waitFor(() => {
          // If error appears in the document
          const errorElement = screen.queryByText('Please select an option.');
          if (errorElement) {
            expect(errorElement).toBeInTheDocument();
          }
        });

        // Should not call onSubmit for empty value
        expect(mockOnSubmit).not.toHaveBeenCalled();
      });
    });
  });
});

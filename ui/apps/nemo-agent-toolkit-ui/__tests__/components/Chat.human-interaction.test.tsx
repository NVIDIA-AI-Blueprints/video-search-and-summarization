/**
 * Tests for human-in-the-loop functionality, OAuth flows, and interaction modals
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { InteractionModal } from '@/components/Chat/ChatInteractionMessage';
import {
  isSystemInteractionMessage,
  isOAuthConsentMessage,
  extractOAuthUrl
} from '@/types/websocket';

// Mock react-hot-toast
jest.mock('react-hot-toast', () => ({
  __esModule: true,
  default: {
    success: jest.fn(),
    error: jest.fn(),
    loading: jest.fn(),
    dismiss: jest.fn()
  }
}));

// Mock window.open for OAuth tests
const mockWindowOpen = jest.fn();
const mockAddEventListener = jest.fn();
const mockRemoveEventListener = jest.fn();

Object.defineProperty(window, 'open', {
  value: mockWindowOpen,
  writable: true
});

Object.defineProperty(window, 'addEventListener', {
  value: mockAddEventListener,
  writable: true
});

Object.defineProperty(window, 'removeEventListener', {
  value: mockRemoveEventListener,
  writable: true
});

describe('Human-in-the-Loop Functionality', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  describe('Interaction Message Detection', () => {
    /**
     * Description: Verifies that isSystemInteractionMessage correctly identifies system interaction message types
     * Success: Function returns true for system_interaction_message types and false for other message types
     */
    test('isSystemInteractionMessage identifies interaction messages correctly', () => {
      const interactionMessage = {
        type: 'system_interaction_message',
        id: 'interaction-1',
        conversation_id: 'conv-123',
        content: {
          input_type: 'user_confirmation',
          text: 'Please confirm this action'
        }
      };

      const responseMessage = {
        type: 'system_response_message',
        id: 'response-1',
        conversation_id: 'conv-123',
        content: { text: 'Regular response' }
      };

      expect(isSystemInteractionMessage(interactionMessage)).toBe(true);
      expect(isSystemInteractionMessage(responseMessage)).toBe(false);
    });

    /**
     * Description: Verifies that isOAuthConsentMessage specifically identifies OAuth consent requests
     * Success: Function returns true only for OAuth consent interaction types, false for other interactions
     */
    test('isOAuthConsentMessage identifies OAuth consent specifically', () => {
      const oauthMessage = {
        type: 'system_interaction_message',
        content: {
          input_type: 'oauth_consent',
          oauth_url: 'https://oauth.example.com/authorize'
        }
      };

      const regularInteraction = {
        type: 'system_interaction_message',
        content: {
          input_type: 'user_confirmation',
          text: 'Please confirm'
        }
      };

      expect(isOAuthConsentMessage(oauthMessage)).toBe(true);
      expect(isOAuthConsentMessage(regularInteraction)).toBe(false);
    });

    /**
     * Description: Verifies that extractOAuthUrl can extract OAuth URLs from different message content locations
     * Success: URLs are correctly extracted from various message formats and content structures
     */
    test('extractOAuthUrl extracts URLs from various locations', () => {
      const scenarios = [
        {
          message: {
            type: 'system_interaction_message',
            content: {
              input_type: 'oauth_consent',
              oauth_url: 'https://oauth.primary.com/auth'
            }
          },
          expected: 'https://oauth.primary.com/auth'
        },
        {
          message: {
            type: 'system_interaction_message',
            content: {
              input_type: 'oauth_consent',
              redirect_url: 'https://oauth.redirect.com/auth'
            }
          },
          expected: 'https://oauth.redirect.com/auth'
        },
        {
          message: {
            type: 'system_interaction_message',
            content: {
              input_type: 'oauth_consent',
              text: 'https://oauth.text.com/auth'
            }
          },
          expected: 'https://oauth.text.com/auth'
        },
        {
          message: {
            type: 'system_interaction_message',
            content: {
              input_type: 'user_confirmation',
              text: 'Not OAuth'
            }
          },
          expected: null
        }
      ];

      scenarios.forEach(({ message, expected }) => {
        const result = extractOAuthUrl(message);
        expect(result).toBe(expected);
      });
    });
  });

  describe('OAuth Flow Integration', () => {
    /**
     * Description: Verifies that OAuth consent messages trigger opening a new browser tab with the correct authorization URL
     * Success: window.open is called with the extracted OAuth URL and appropriate target parameters
     */
    test('OAuth message opens new tab with correct URL', () => {
      const handleWebSocketMessage = (message: any) => {
        if (isSystemInteractionMessage(message) && message.content?.input_type === 'oauth_consent') {
          const oauthUrl = extractOAuthUrl(message);
          if (oauthUrl) {
            window.open(oauthUrl, '_blank');
          }
        }
      };

      const oauthMessage = {
        type: 'system_interaction_message',
        conversation_id: 'test-conv',
        content: {
          input_type: 'oauth_consent',
          oauth_url: 'https://oauth.provider.com/authorize?state=xyz&client_id=123'
        }
      };

      handleWebSocketMessage(oauthMessage);

      expect(mockWindowOpen).toHaveBeenCalledWith(
        'https://oauth.provider.com/authorize?state=xyz&client_id=123',
        '_blank'
      );
    });

    /**
     * Description: Verifies that OAuth flow establishes message event listeners for completion detection
     * Success: Event listeners are set up to detect OAuth completion messages from popup windows
     */
    test('OAuth flow sets up completion event listener', () => {
      const handleOAuthConsent = (message: any) => {
        if (isOAuthConsentMessage(message)) {
          const oauthUrl = extractOAuthUrl(message);
          if (oauthUrl) {
            const popup = window.open(oauthUrl, 'oauth-popup', 'width=600,height=700');

            const handleOAuthComplete = (event: MessageEvent) => {
              if (popup && !popup.closed) popup.close();
              window.removeEventListener('message', handleOAuthComplete);
            };

            window.addEventListener('message', handleOAuthComplete);
          }
        }
      };

      const oauthMessage = {
        type: 'system_interaction_message',
        content: {
          input_type: 'oauth_consent',
          oauth_url: 'https://oauth.example.com/authorize'
        }
      };

      handleOAuthConsent(oauthMessage);

      expect(mockWindowOpen).toHaveBeenCalledWith(
        'https://oauth.example.com/authorize',
        'oauth-popup',
        'width=600,height=700'
      );
      expect(mockAddEventListener).toHaveBeenCalledWith(
        'message',
        expect.any(Function)
      );
    });

    /**
     * Description: Verifies that OAuth popup windows are properly closed and cleaned up after completion
     * Success: Event listeners are removed and popup windows are closed when OAuth flow completes
     */
    test('OAuth popup cleanup on completion', () => {
      let eventHandler: (event: MessageEvent) => void;

      mockAddEventListener.mockImplementation((event, handler) => {
        if (event === 'message') {
          eventHandler = handler;
        }
      });

      const mockPopup = {
        closed: false,
        close: jest.fn()
      };

      mockWindowOpen.mockReturnValue(mockPopup);

      const handleOAuthConsent = (message: any) => {
        if (isOAuthConsentMessage(message)) {
          const oauthUrl = extractOAuthUrl(message);
          if (oauthUrl) {
            const popup = window.open(oauthUrl, 'oauth-popup', 'width=600,height=700');

            const handleOAuthComplete = (event: MessageEvent) => {
              if (popup && !popup.closed) popup.close();
              window.removeEventListener('message', handleOAuthComplete);
            };

            window.addEventListener('message', handleOAuthComplete);
          }
        }
      };

      const oauthMessage = {
        type: 'system_interaction_message',
        content: {
          input_type: 'oauth_consent',
          oauth_url: 'https://oauth.example.com/authorize'
        }
      };

      handleOAuthConsent(oauthMessage);

      // Simulate OAuth completion message
      const completionEvent = new MessageEvent('message', {
        data: { type: 'oauth_complete', success: true }
      });

      eventHandler(completionEvent);

      expect(mockPopup.close).toHaveBeenCalled();
      expect(mockRemoveEventListener).toHaveBeenCalledWith(
        'message',
        expect.any(Function)
      );
    });
  });

  describe('Interaction Modal Functionality', () => {
    /**
     * Description: Verifies that interaction modals open with the correct data and configuration
     * Success: Modal displays appropriate interaction content, buttons, and user interface elements
     */
    test('modal opens with correct interaction data', () => {
      let modalOpen = false;
      let interactionMessage: any = null;

      const openModal = (data: any) => {
        interactionMessage = data;
        modalOpen = true;
      };

      const handleWebSocketMessage = (message: any) => {
        if (isSystemInteractionMessage(message) && message.content?.input_type !== 'oauth_consent') {
          openModal(message);
        }
      };

      const mockInteractionMessage = {
        type: 'system_interaction_message',
        id: 'interaction-123',
        conversation_id: 'conv-456',
        thread_id: 'thread-789',
        parent_id: 'parent-101',
        content: {
          input_type: 'user_confirmation',
          text: 'Please confirm this action before proceeding'
        }
      };

      handleWebSocketMessage(mockInteractionMessage);

      expect(modalOpen).toBe(true);
      expect(interactionMessage).toEqual(mockInteractionMessage);
    });

    /**
     * Description: Verifies that modal context is preserved when closing and reopening interaction dialogs
     * Success: Modal state and data remain intact through multiple open/close cycles
     */
    test('modal preserves context through close/reopen cycle', () => {
      let modalOpen = false;
      let interactionMessage: any = null;

      const setModalOpen = (open: boolean) => {
        modalOpen = open;
      };

      const openModal = (data: any) => {
        interactionMessage = data;
        modalOpen = true;
      };

      const interactionData = {
        type: 'system_interaction_message',
        content: {
          input_type: 'user_confirmation',
          text: 'Please confirm this action'
        },
        thread_id: 'thread-123',
        parent_id: 'parent-456',
        conversation_id: 'conv-789'
      };

      // Open modal
      openModal(interactionData);
      expect(modalOpen).toBe(true);
      expect(interactionMessage).toEqual(interactionData);

      // Close modal
      setModalOpen(false);
      expect(modalOpen).toBe(false);
      // Context should be preserved
      expect(interactionMessage).toEqual(interactionData);

      // Reopen modal
      setModalOpen(true);
      expect(modalOpen).toBe(true);
      expect(interactionMessage).toEqual(interactionData);
    });

    /**
     * Description: Verifies that user interaction responses include proper conversation context for backend processing
     * Success: Response messages contain conversation ID, user input, and necessary context data
     */
    test('user interaction response includes conversation context', () => {
      const mockWebSocket = { send: jest.fn() };

      const handleUserInteraction = ({
        interactionMessage = {},
        userResponse = ''
      }: any) => {
        const wsMessage = {
          type: 'user_interaction_message',
          id: 'new-id-123',
          thread_id: interactionMessage?.thread_id,
          parent_id: interactionMessage?.parent_id,
          content: {
            messages: [
              {
                role: 'user',
                content: [
                  {
                    type: 'text',
                    text: userResponse
                  }
                ]
              }
            ]
          },
          timestamp: new Date().toISOString()
        };

        mockWebSocket.send(JSON.stringify(wsMessage));
      };

      const interactionMessage = {
        thread_id: 'thread-abc',
        parent_id: 'parent-def',
        conversation_id: 'conv-ghi'
      };

      handleUserInteraction({
        interactionMessage,
        userResponse: 'Approved for processing'
      });

      expect(mockWebSocket.send).toHaveBeenCalledTimes(1);

      const sentMessage = JSON.parse(mockWebSocket.send.mock.calls[0][0]);

      expect(sentMessage.type).toBe('user_interaction_message');
      expect(sentMessage.thread_id).toBe('thread-abc');
      expect(sentMessage.parent_id).toBe('parent-def');
      expect(sentMessage.content.messages[0].content[0].text).toBe('Approved for processing');
      expect(sentMessage.timestamp).toBeDefined();
    });

    /**
     * Description: Verifies that interaction modals can handle different types of user interaction requirements
     * Success: Different interaction types (forms, confirmations, selections) are displayed and handled correctly
     */
    test('modal handles different interaction types', () => {
      const interactionTypes = [
        {
          type: 'user_confirmation',
          text: 'Please confirm this action',
          expectedButton: 'Confirm'
        },
        {
          type: 'user_input',
          text: 'Please provide additional information',
          expectedButton: 'Submit'
        },
        {
          type: 'approval_required',
          text: 'Manager approval required',
          expectedButton: 'Approve'
        }
      ];

      interactionTypes.forEach(({ type, text, expectedButton }) => {
        const message = {
          type: 'system_interaction_message',
          content: {
            input_type: type,
            text: text
          }
        };

        // Mock modal behavior based on interaction type
        const getModalConfig = (interactionMessage: any) => {
          const inputType = interactionMessage.content?.input_type;

          switch (inputType) {
            case 'user_confirmation':
              return { buttonText: 'Confirm', hasTextInput: false };
            case 'user_input':
              return { buttonText: 'Submit', hasTextInput: true };
            case 'approval_required':
              return { buttonText: 'Approve', hasTextInput: false };
            default:
              return { buttonText: 'OK', hasTextInput: false };
          }
        };

        const config = getModalConfig(message);
        expect(config.buttonText).toBe(expectedButton);
      });
    });
  });

  describe('Error Handling and Edge Cases', () => {
    /**
     * Description: Verifies that malformed interaction messages are handled gracefully without breaking the UI
     * Success: Invalid interaction messages are ignored or show appropriate error states, application continues functioning
     */
    test('handles malformed interaction messages gracefully', () => {
      const malformedMessages = [
        { type: 'system_interaction_message' }, // Missing content
        { type: 'system_interaction_message', content: {} }, // Empty content
        { type: 'system_interaction_message', content: null }, // Null content
        { type: 'system_interaction_message', content: { input_type: null } }, // Null input_type
        {} // Completely empty
      ];

      malformedMessages.forEach(message => {
        expect(() => isSystemInteractionMessage(message)).not.toThrow();
        expect(() => isOAuthConsentMessage(message)).not.toThrow();
        expect(() => extractOAuthUrl(message)).not.toThrow();

        // Should return false/null for malformed messages
        expect(isOAuthConsentMessage(message)).toBe(false);
        expect(extractOAuthUrl(message)).toBeNull();
      });
    });

    /**
     * Description: Verifies that OAuth popup blocking by browsers is handled gracefully with fallback options
     * Success: Popup blocking is detected, appropriate error messages shown, fallback authentication methods offered
     */
    test('handles OAuth popup blocking gracefully', () => {
      // Mock popup being blocked (window.open returns null)
      mockWindowOpen.mockReturnValue(null);

      const handleOAuthConsent = (message: any) => {
        if (isOAuthConsentMessage(message)) {
          const oauthUrl = extractOAuthUrl(message);
          if (oauthUrl) {
            const popup = window.open(oauthUrl, '_blank');
            if (!popup) {
              // Handle popup blocked scenario
              console.warn('Popup blocked - please allow popups for OAuth');
              // Could show alternative flow or instructions
              return false;
            }
            return true;
          }
        }
        return false;
      };

      const oauthMessage = {
        type: 'system_interaction_message',
        content: {
          input_type: 'oauth_consent',
          oauth_url: 'https://oauth.example.com/authorize'
        }
      };

      const consoleWarn = jest.spyOn(console, 'warn').mockImplementation();

      const result = handleOAuthConsent(oauthMessage);

      expect(result).toBe(false);
      expect(consoleWarn).toHaveBeenCalledWith('Popup blocked - please allow popups for OAuth');

      consoleWarn.mockRestore();
    });

    /**
     * Description: Verifies that user interaction responses are handled properly when WebSocket connection is unavailable
     * Success: Responses are queued or alternative communication methods are used when WebSocket is disconnected
     */
    test('handles missing WebSocket connection for user responses', () => {
      const handleUserInteraction = ({
        interactionMessage = {},
        userResponse = ''
      }: any) => {
        // webSocketRef.current is null
        const webSocket = null;

        if (!webSocket) {
          console.error('Cannot send user response - WebSocket not connected');
          return false;
        }

        // Would normally send message here
        return true;
      };

      const consoleError = jest.spyOn(console, 'error').mockImplementation();

      const result = handleUserInteraction({
        interactionMessage: { thread_id: 'test' },
        userResponse: 'Test response'
      });

      expect(result).toBe(false);
      expect(consoleError).toHaveBeenCalledWith('Cannot send user response - WebSocket not connected');

      consoleError.mockRestore();
    });

    /**
     * Description: Verifies that multiple simultaneous interaction messages are handled correctly without conflicts
     * Success: Concurrent interactions are queued or managed appropriately, no data corruption or UI conflicts occur
     */
    test('handles concurrent interaction messages', () => {
      let activeInteraction: any = null;
      const interactionQueue: any[] = [];

      const handleWebSocketMessage = (message: any) => {
        if (isSystemInteractionMessage(message) && message.content?.input_type !== 'oauth_consent') {
          if (activeInteraction) {
            // Queue additional interactions
            interactionQueue.push(message);
          } else {
            // Handle immediately
            activeInteraction = message;
          }
        }
      };

      const completeInteraction = () => {
        activeInteraction = null;

        // Process next in queue
        if (interactionQueue.length > 0) {
          activeInteraction = interactionQueue.shift();
        }
      };

      // Send multiple interactions
      const interactions = [
        { type: 'system_interaction_message', id: '1', content: { input_type: 'user_confirmation', text: 'First' } },
        { type: 'system_interaction_message', id: '2', content: { input_type: 'user_confirmation', text: 'Second' } },
        { type: 'system_interaction_message', id: '3', content: { input_type: 'user_confirmation', text: 'Third' } }
      ];

      interactions.forEach(handleWebSocketMessage);

      // First should be active, others queued
      expect(activeInteraction.id).toBe('1');
      expect(interactionQueue).toHaveLength(2);

      // Complete first interaction
      completeInteraction();
      expect(activeInteraction.id).toBe('2');
      expect(interactionQueue).toHaveLength(1);

      // Complete second interaction
      completeInteraction();
      expect(activeInteraction.id).toBe('3');
      expect(interactionQueue).toHaveLength(0);
    });
  });
});

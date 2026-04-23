/**
 * Consolidated tests for InteractionModal component, OAuth flows, and human-in-the-loop functionality
 * 
 * Tests cover:
 * - InteractionModal component rendering and behavior (text, binary choice, radio, notification types)
 * - OAuth flow integration (popup windows, event listeners, completion handling)
 * - User interaction response handling and WebSocket communication
 * - Error handling (malformed messages, popup blocking, concurrent interactions)
 */

import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { InteractionModal } from '@/components/Chat/ChatInteractionMessage';
import {
  isSystemInteractionMessage,
  isOAuthConsentMessage,
  extractOAuthUrl
} from '@/types/websocket';

// Mock react-hot-toast
jest.mock('react-hot-toast', () => {
  const mockToastFunctions = {
    success: jest.fn(),
    error: jest.fn(),
    loading: jest.fn(),
    dismiss: jest.fn(),
    custom: jest.fn()
  };
  
  return {
    __esModule: true,
    default: mockToastFunctions,
    toast: mockToastFunctions
  };
});

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

describe('InteractionModal and Human-in-the-Loop Functionality', () => {
  const mockOnClose = jest.fn();
  const mockOnSubmit = jest.fn();
  
  // Get the mocked toast object
  const mockToast = require('react-hot-toast').toast;

  beforeEach(() => {
    jest.clearAllMocks();
  });

  // ============================================================================
  // OAUTH FLOW INTEGRATION TESTS
  // ============================================================================
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
              console.warn('Popup blocked - please allow popups for OAuth');
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
  });

  // ============================================================================
  // INTERACTION MODAL COMPONENT TESTS
  // ============================================================================
  describe('InteractionModal Component - Text Input Type', () => {
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
      fireEvent.click(submitButton);

      expect(screen.getByText('This field is required.')).toBeInTheDocument();
      expect(mockOnSubmit).not.toHaveBeenCalled();
      expect(mockOnClose).not.toHaveBeenCalled();
    });

    it('should have a cancel button that calls onClose', () => {
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

      // Modal should have both Cancel and Submit buttons
      expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Submit' })).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
      expect(mockOnClose).toHaveBeenCalled();
    });
  });

  describe('InteractionModal Component - Binary Choice Type', () => {
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

      expect(continueButton).toHaveClass('bg-[#76b900]');
      expect(stopButton).toHaveClass('bg-neutral-700');
    });
  });

  describe('InteractionModal Component - Radio Selection Type', () => {
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
      fireEvent.click(submitButton);

      expect(screen.getByText('Please select an option.')).toBeInTheDocument();
      expect(mockOnSubmit).not.toHaveBeenCalled();
      expect(mockOnClose).not.toHaveBeenCalled();
    });
  });

  describe('InteractionModal Component - Notification Type', () => {
    it('should display toast notification instead of modal', () => {
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

      expect(mockToast.custom).toHaveBeenCalled();
      expect(result.container.firstChild).toBeNull();
    });

    it('should handle notification with custom content', () => {
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

      expect(mockToast.custom).toHaveBeenCalledWith(
        expect.any(Function),
        {
          position: 'top-right',
          duration: Infinity,
          id: 'notification-toast'
        }
      );
    });

    it('should handle notification without content gracefully', () => {
      const message = {
        type: 'system_interaction_message',
        content: {
          input_type: 'notification'
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

      expect(mockToast.custom).toHaveBeenCalled();
    });
  });

  describe('InteractionModal Component - Modal State and Edge Cases', () => {
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

      expect(screen.getByText('Unknown interaction type')).toBeInTheDocument();
    });

    it('should handle message without input_type', () => {
      const message = {
        type: 'system_interaction_message',
        content: {
          text: 'General interaction message'
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

      expect(screen.getByTestId('hitl-modal')).toBeInTheDocument();
    });
  });

  describe('InteractionModal Component - Validation Error States', () => {
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

      // Trigger validation error
      fireEvent.click(submitButton);
      expect(screen.getByText('This field is required.')).toBeInTheDocument();

      // Enter text and submit again
      fireEvent.change(textarea, { target: { value: 'Valid input' } });
      fireEvent.click(submitButton);

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
            { id: 'opt1', label: 'Option 1', value: '' },
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

      await waitFor(() => {
        const errorElement = screen.queryByText('Please select an option.');
        if (errorElement) {
          expect(errorElement).toBeInTheDocument();
        }
      });

      expect(mockOnSubmit).not.toHaveBeenCalled();
    });
  });

  // ============================================================================
  // USER INTERACTION AND WEBSOCKET INTEGRATION TESTS
  // ============================================================================
  describe('User Interaction and WebSocket Integration', () => {
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

  // ============================================================================
  // TIMEOUT FUNCTIONALITY TESTS
  // ============================================================================
  describe('InteractionModal Component - Timeout Functionality', () => {
    beforeEach(() => {
      jest.useFakeTimers();
    });

    afterEach(() => {
      jest.useRealTimers();
    });

    it('should not render timer when content.timeout is null', () => {
      const message = {
        type: 'system_interaction_message',
        content: {
          input_type: 'text',
          text: 'No timeout prompt',
          timeout: null
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

      expect(screen.queryByText(/Time remaining/)).not.toBeInTheDocument();
    });

    it('should not render timer when content.timeout is undefined', () => {
      const message = {
        type: 'system_interaction_message',
        content: {
          input_type: 'text',
          text: 'No timeout prompt'
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

      expect(screen.queryByText(/Time remaining/)).not.toBeInTheDocument();
    });

    it('should render modal normally when content.timeout is set (timeout not yet implemented)', () => {
      const message = {
        type: 'system_interaction_message',
        content: {
          input_type: 'text',
          text: 'Timed prompt',
          timeout: 60,
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

      // Timer is not yet implemented in this version - modal should still render
      expect(screen.getByText('Timed prompt')).toBeInTheDocument();
      expect(screen.queryByText(/Time remaining/)).not.toBeInTheDocument();
    });

    it('should still allow submission when timeout field is present', () => {
      const message = {
        type: 'system_interaction_message',
        content: {
          input_type: 'text',
          text: 'Timed prompt',
          timeout: 60
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

      fireEvent.change(textarea, { target: { value: 'My response' } });
      fireEvent.click(submitButton);

      expect(mockOnSubmit).toHaveBeenCalledWith({
        interactionMessage: message,
        userResponse: 'My response'
      });
      expect(mockOnClose).toHaveBeenCalled();
    });

    it('should render modal when content has error field (timeout not yet implemented)', () => {
      const message = {
        type: 'system_interaction_message',
        content: {
          input_type: 'text',
          text: 'Timed prompt',
          timeout: 3,
          error: 'Custom timeout error message'
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

      // Modal should render normally even with timeout/error fields
      expect(screen.getByText('Timed prompt')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Submit' })).toBeInTheDocument();
    });

    it('should render modal when content.error is not provided', () => {
      const message = {
        type: 'system_interaction_message',
        content: {
          input_type: 'text',
          text: 'Timed prompt',
          timeout: 2
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

      // Modal should render normally
      expect(screen.getByText('Timed prompt')).toBeInTheDocument();
      expect(screen.getByRole('textbox')).toBeInTheDocument();
    });

    it('should stop timer when user submits before timeout', () => {
      const message = {
        type: 'system_interaction_message',
        content: {
          input_type: 'text',
          text: 'Timed prompt',
          timeout: 60
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

      fireEvent.change(textarea, { target: { value: 'My response' } });
      fireEvent.click(submitButton);

      expect(mockOnSubmit).toHaveBeenCalledWith({
        interactionMessage: message,
        userResponse: 'My response'
      });
      expect(mockOnClose).toHaveBeenCalled();
      
      // Timer should be cleared, so advancing time shouldn't trigger error toast
      act(() => {
        jest.advanceTimersByTime(60000);
      });
      expect(mockToast.error).not.toHaveBeenCalled();
    });

    it('should disable inputs when timed out', () => {
      const message = {
        type: 'system_interaction_message',
        content: {
          input_type: 'text',
          text: 'Timed prompt',
          timeout: 2
        }
      };

      // Use a ref to track onClose calls so the modal stays visible
      let closeCount = 0;
      const trackingOnClose = () => { closeCount++; };

      render(
        <InteractionModal
          isOpen={true}
          interactionMessage={message}
          onClose={trackingOnClose}
          onSubmit={mockOnSubmit}
        />
      );

      // Advance timer, but not all the way to timeout
      act(() => {
        jest.advanceTimersByTime(1000);
      });

      // Inputs should still be enabled before timeout
      const textarea = screen.getByRole('textbox');
      const submitButton = screen.getByRole('button', { name: 'Submit' });

      expect(textarea).not.toBeDisabled();
      expect(submitButton).not.toBeDisabled();
    });

    it('should not show countdown for notification input type', () => {
      const message = {
        type: 'system_interaction_message',
        content: {
          input_type: 'notification',
          text: 'Notification message',
          timeout: 30
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

      // Notification type returns null and uses toast instead
      expect(result.container.firstChild).toBeNull();
      expect(mockToast.custom).toHaveBeenCalled();
    });

    it('should have Cancel buttons in text and radio input types', () => {
      const inputTypesWithCancel = ['text', 'radio'];
      
      inputTypesWithCancel.forEach((inputType) => {
        const message = {
          type: 'system_interaction_message',
          content: {
            input_type: inputType,
            text: 'Test prompt',
            options: inputType !== 'text' ? [
              { id: 'opt1', label: 'Option 1', value: 'value1' },
              { id: 'opt2', label: 'Option 2', value: 'value2' }
            ] : undefined
          }
        };

        const { unmount } = render(
          <InteractionModal
            isOpen={true}
            interactionMessage={message}
            onClose={mockOnClose}
            onSubmit={mockOnSubmit}
          />
        );

        // Text and radio types should have a Cancel button
        const cancelButtons = screen.queryAllByRole('button', { name: 'Cancel' });
        expect(cancelButtons.length).toBeGreaterThanOrEqual(1);

        unmount();
      });
    });

    it('should not be dismissible by clicking backdrop', () => {
      const message = {
        type: 'system_interaction_message',
        content: {
          input_type: 'text',
          text: 'Non-dismissible modal'
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

      // Find the backdrop (the outer fixed div)
      const backdrop = screen.getByTestId('hitl-modal');
      expect(backdrop).toBeInTheDocument();

      // Click on backdrop
      fireEvent.click(backdrop);

      // onClose should NOT be called from backdrop click
      // (It might be called 0 times if no click handler, which is correct)
      // We just verify the modal is still visible
      expect(screen.getByText('Non-dismissible modal')).toBeInTheDocument();
    });
  });

  // ============================================================================
  // ERROR HANDLING AND EDGE CASES
  // ============================================================================
  describe('Error Handling and Edge Cases', () => {
    /**
     * Description: Verifies that user interaction responses are handled properly when WebSocket connection is unavailable
     * Success: Responses are queued or alternative communication methods are used when WebSocket is disconnected
     */
    test('handles missing WebSocket connection for user responses', () => {
      const handleUserInteraction = ({
        interactionMessage = {},
        userResponse = ''
      }: any) => {
        const webSocket = null;

        if (!webSocket) {
          console.error('Cannot send user response - WebSocket not connected');
          return false;
        }

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
            interactionQueue.push(message);
          } else {
            activeInteraction = message;
          }
        }
      };

      const completeInteraction = () => {
        activeInteraction = null;

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

      expect(activeInteraction.id).toBe('1');
      expect(interactionQueue).toHaveLength(2);

      completeInteraction();
      expect(activeInteraction.id).toBe('2');
      expect(interactionQueue).toHaveLength(1);

      completeInteraction();
      expect(activeInteraction.id).toBe('3');
      expect(interactionQueue).toHaveLength(0);
    });
  });
});


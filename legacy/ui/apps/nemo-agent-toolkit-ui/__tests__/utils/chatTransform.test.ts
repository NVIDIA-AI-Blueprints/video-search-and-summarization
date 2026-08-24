/**
 * Unit tests for pure chat transformation helpers
 * These tests verify the core business logic without side effects
 */

import {
  shouldAppendResponse,
  appendAssistantText,
  mergeIntermediateSteps,
  applyMessageUpdate,
  createAssistantMessage,
  updateAssistantMessage,
  shouldRenderAssistantMessage,
  extractConversationContent,
} from '@/utils/chatTransform';

import {
  SystemResponseMessage,
  SystemIntermediateMessage,
  ErrorMessage,
  IntermediateStep,
} from '@/types/websocket';

import { Message, Conversation } from '@/types/chat';

describe('chatTransform', () => {
  describe('shouldAppendResponse', () => {
    it('returns true for system_response_message with in_progress status and text content', () => {
      const message: SystemResponseMessage = {
        type: 'system_response_message',
        status: 'in_progress',
        content: { text: 'Hello world' },
        conversation_id: 'test',
      };

      expect(shouldAppendResponse(message)).toBe(true);
    });

    it('returns false for system_response_message with complete status', () => {
      const message: SystemResponseMessage = {
        type: 'system_response_message',
        status: 'complete',
        content: { text: 'Hello world' },
        conversation_id: 'test',
      };

      expect(shouldAppendResponse(message)).toBe(false);
    });

    it('returns false for system_response_message with empty text', () => {
      const message: SystemResponseMessage = {
        type: 'system_response_message',
        status: 'in_progress',
        content: { text: '' },
        conversation_id: 'test',
      };

      expect(shouldAppendResponse(message)).toBe(false);
    });

    it('returns false for system_response_message with whitespace-only text', () => {
      const message: SystemResponseMessage = {
        type: 'system_response_message',
        status: 'in_progress',
        content: { text: '   \n\t  ' },
        conversation_id: 'test',
      };

      expect(shouldAppendResponse(message)).toBe(false);
    });

    it('returns false for non-system_response_message types', () => {
      const message: ErrorMessage = {
        type: 'error',
        content: { text: 'Error occurred' },
        conversation_id: 'test',
      };

      expect(shouldAppendResponse(message)).toBe(false);
    });
  });

  describe('appendAssistantText', () => {
    it('concatenates to existing non-empty content', () => {
      const result = appendAssistantText('Hello', ' world');
      expect(result).toBe('Hello world');
    });

    it('replaces empty content with new text', () => {
      const result = appendAssistantText('', 'Hello world');
      expect(result).toBe('Hello world');
    });

    it('replaces FAIL placeholder with new text', () => {
      const result = appendAssistantText('FAIL', 'Hello world');
      expect(result).toBe('Hello world');
    });

    it('returns previous content when new text is empty', () => {
      const result = appendAssistantText('Existing content', '');
      expect(result).toBe('Existing content');
    });

    it('returns previous content when new text is whitespace only', () => {
      const result = appendAssistantText('Existing content', '   \n  ');
      expect(result).toBe('Existing content');
    });

    it('handles null/undefined inputs gracefully', () => {
      // @ts-expect-error Testing runtime behavior
      const result = appendAssistantText(null, 'test');
      expect(result).toBe('test');
    });
  });

  describe('applyMessageUpdate', () => {
      const baseConversation: Conversation = {
    id: 'conv-1',
    name: 'New Conversation',
    messages: [],
    prompt: '',
    temperature: 0.7,
    folderId: null,
  };

    it('updates conversation with new messages immutably', () => {
      const newMessages: Message[] = [
        { role: 'user', content: 'Hello', id: 'msg-1' },
        { role: 'assistant', content: 'Hi there', id: 'msg-2' },
      ];

      const result = applyMessageUpdate(baseConversation, newMessages);

      expect(result).not.toBe(baseConversation); // Immutability check
      expect(result.messages).toBe(newMessages);
      expect(result.id).toBe(baseConversation.id);
    });

    it('updates conversation title from first user message', () => {
      const newMessages: Message[] = [
        { role: 'user', content: 'What is the weather like today?', id: 'msg-1' },
        { role: 'assistant', content: "It's sunny!", id: 'msg-2' },
      ];

      const result = applyMessageUpdate(baseConversation, newMessages);

      expect(result.name).toBe('What is the weather like today');
    });

    it('truncates long conversation titles to 30 characters', () => {
      const longMessage = 'This is a very long user message that should be truncated to 30 characters';
      const newMessages: Message[] = [
        { role: 'user', content: longMessage, id: 'msg-1' },
      ];

      const result = applyMessageUpdate(baseConversation, newMessages);

      expect(result.name).toBe(longMessage.substring(0, 30));
      expect(result.name.length).toBe(30);
    });

    it('does not update title if not "New Conversation"', () => {
      const conversationWithTitle = { ...baseConversation, name: 'Existing Title' };
      const newMessages: Message[] = [
        { role: 'user', content: 'New message', id: 'msg-1' },
      ];

      const result = applyMessageUpdate(conversationWithTitle, newMessages);

      expect(result.name).toBe('Existing Title');
    });

    it('does not update title if no user messages', () => {
      const newMessages: Message[] = [
        { role: 'assistant', content: 'Hello', id: 'msg-1' },
      ];

      const result = applyMessageUpdate(baseConversation, newMessages);

      expect(result.name).toBe('New Conversation');
    });
  });

  describe('createAssistantMessage', () => {
    it('creates assistant message with required fields', () => {
      const message = createAssistantMessage('msg-1', 'parent-1', 'Hello');

      expect(message.role).toBe('assistant');
      expect(message.id).toBe('msg-1');
      expect(message.parentId).toBe('parent-1');
      expect(message.content).toBe('Hello');
      expect(message.intermediateSteps).toEqual([]);
      expect(message.humanInteractionMessages).toEqual([]);
      expect(message.errorMessages).toEqual([]);
      expect(typeof message.timestamp).toBe('number');
    });

    it('creates assistant message with optional arrays', () => {
      const steps: IntermediateStep[] = [{ id: 'step-1' }];
      const interactions = [{ type: 'interaction' }];
      const errors = [{ type: 'error' }];

      const message = createAssistantMessage(
        'msg-1',
        'parent-1',
        'Hello',
        steps,
        interactions,
        errors
      );

      expect(message.intermediateSteps).toBe(steps);
      expect(message.humanInteractionMessages).toBe(interactions);
      expect(message.errorMessages).toBe(errors);
    });

    it('defaults to empty content when not provided', () => {
      const message = createAssistantMessage('msg-1');

      expect(message.content).toBe('');
      expect(message.id).toBe('msg-1');
      expect(message.parentId).toBeUndefined();
    });
  });

  describe('updateAssistantMessage', () => {
    const baseMessage: Message = {
      role: 'assistant',
      content: 'Original content',
      id: 'msg-1',
      intermediateSteps: [],
      timestamp: 1000,
    };

    it('updates content immutably', () => {
      const result = updateAssistantMessage(baseMessage, 'New content');

      expect(result).not.toBe(baseMessage); // Immutability
      expect(result.content).toBe('New content');
      expect(result.id).toBe(baseMessage.id);
      expect(result.timestamp).toBeGreaterThan(baseMessage.timestamp!);
    });

    it('updates intermediate steps immutably', () => {
      const newSteps: IntermediateStep[] = [{ id: 'step-1' }];
      const result = updateAssistantMessage(baseMessage, undefined, newSteps);

      expect(result.intermediateSteps).toBe(newSteps);
      expect(result.content).toBe(baseMessage.content); // Unchanged
    });

    it('preserves original content when not provided', () => {
      const result = updateAssistantMessage(baseMessage);

      expect(result.content).toBe(baseMessage.content);
      expect(result.timestamp).toBeGreaterThan(baseMessage.timestamp!);
    });

    it('handles empty content gracefully', () => {
      const messageWithEmptyContent = { ...baseMessage, content: '' };
      const result = updateAssistantMessage(messageWithEmptyContent);

      expect(result.content).toBe('');
    });
  });

  describe('shouldRenderAssistantMessage', () => {
    it('always renders non-assistant messages', () => {
      const userMessage: Message = { role: 'user', content: 'Hello', id: 'msg-1' };
      expect(shouldRenderAssistantMessage(userMessage)).toBe(true);
    });

    it('renders assistant messages with content', () => {
      const message: Message = { role: 'assistant', content: 'Hello', id: 'msg-1' };
      expect(shouldRenderAssistantMessage(message)).toBe(true);
    });

    it('renders assistant messages with intermediate steps', () => {
      const message: Message = {
        role: 'assistant',
        content: '',
        id: 'msg-1',
        intermediateSteps: [{ id: 'step-1' }],
      };
      expect(shouldRenderAssistantMessage(message)).toBe(true);
    });

    it('does not render assistant messages without content or steps', () => {
      const message: Message = {
        role: 'assistant',
        content: '',
        id: 'msg-1',
        intermediateSteps: [],
      };
      expect(shouldRenderAssistantMessage(message)).toBe(false);
    });

    it('does not render assistant messages with whitespace-only content', () => {
      const message: Message = {
        role: 'assistant',
        content: '   \n\t  ',
        id: 'msg-1',
        intermediateSteps: [],
      };
      expect(shouldRenderAssistantMessage(message)).toBe(false);
    });
  });

  describe('extractConversationContent', () => {
    it('extracts content from last message', () => {
      const conversation: Conversation = {
        id: 'conv-1',
        name: 'Test',
        messages: [
          { role: 'user', content: 'Hello', id: 'msg-1' },
          { role: 'assistant', content: 'Hi there', id: 'msg-2' },
        ],
        prompt: '',
        temperature: 0.7,
        folderId: null,
      };

      const result = extractConversationContent(conversation);
      expect(result).toBe('Hi there');
    });

    it('returns empty string for conversation with no messages', () => {
      const conversation: Conversation = {
        id: 'conv-1',
        name: 'Test',
        messages: [],

        prompt: '',
        temperature: 0.7,
        folderId: null,
      };

      const result = extractConversationContent(conversation);
      expect(result).toBe('');
    });

    it('handles undefined content gracefully', () => {
      const conversation: Conversation = {
        id: 'conv-1',
        name: 'Test',
        messages: [{ role: 'user', content: undefined as any, id: 'msg-1' }],

        prompt: '',
        temperature: 0.7,
        folderId: null,
      };

      const result = extractConversationContent(conversation);
      expect(result).toBe('');
    });
  });
});
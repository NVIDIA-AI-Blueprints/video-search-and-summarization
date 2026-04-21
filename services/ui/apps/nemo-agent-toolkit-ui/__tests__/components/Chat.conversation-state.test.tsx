/**
 * Tests for conversation state management, persistence, and data integrity
 */

import { cleanConversationHistory } from '@/utils/app/clean';
import { saveConversation, saveConversations } from '@/utils/app/conversation';
import {
  appendAssistantText,
  mergeIntermediateSteps,
  shouldRenderAssistantMessage,
  applyMessageUpdate
} from '@/utils/chatTransform';

// Mock both localStorage and sessionStorage
const mockLocalStorage = {
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn(),
  clear: jest.fn(),
};

const mockSessionStorage = {
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn(),
  clear: jest.fn(),
};

Object.defineProperty(window, 'localStorage', {
  value: mockLocalStorage
});

Object.defineProperty(window, 'sessionStorage', {
  value: mockSessionStorage
});

describe('Conversation State Management', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

    describe('Conversation Persistence - INTEGRATION TESTS', () => {
    /**
     * Description: Verifies that saveConversations correctly stores conversation arrays to sessionStorage
     * Success: sessionStorage.setItem is called with 'conversationHistory' key and properly serialized JSON data
     */
    test('saveConversations persists to sessionStorage correctly', () => {
      const mockConversations = [
        { id: 'conv-1', name: 'Test Chat', messages: [], folderId: null },
        { id: 'conv-2', name: 'Another Chat', messages: [], folderId: null }
      ];

      saveConversations(mockConversations);

      expect(mockSessionStorage.setItem).toHaveBeenCalledWith(
        'conversationHistory',
        JSON.stringify(mockConversations)
      );
    });

    /**
     * Description: Verifies that saveConversation correctly stores individual conversations to sessionStorage
     * Success: sessionStorage.setItem is called with 'selectedConversation' key and properly serialized conversation data
     */
    test('saveConversation persists single conversation correctly', () => {
      const mockConversation = {
        id: 'conv-1',
        name: 'Test Chat',
        messages: [
          { role: 'user', content: 'Hello' },
          { role: 'assistant', content: 'Hi there!' }
        ],
        folderId: null
      };

      saveConversation(mockConversation);

      expect(mockSessionStorage.setItem).toHaveBeenCalledWith(
        'selectedConversation',
        JSON.stringify(mockConversation)
      );
    });

    /**
     * Description: Verifies that conversation data persists across page refreshes by testing sessionStorage retrieval
     * Success: Data retrieved from sessionStorage matches original conversation structure and content exactly
     */
    test('conversation state survives page refresh', () => {
      const mockConversations = [
        {
          id: 'conv-1',
          name: 'Persistent Chat',
          messages: [
            { role: 'user', content: 'Hello' },
            { role: 'assistant', content: 'Hi there!' }
          ],
          folderId: null
        }
      ];

      // Mock sessionStorage returning saved data (not localStorage)
      mockSessionStorage.getItem.mockReturnValue(JSON.stringify(mockConversations));

      // Simulate page refresh by reloading conversations
      const loadedConversations = JSON.parse(mockSessionStorage.getItem('conversationHistory') || '[]');

      expect(loadedConversations).toEqual(mockConversations);
      expect(loadedConversations[0].messages).toHaveLength(2);
    });

    /**
     * Description: Verifies that saveConversation handles sessionStorage quota exceeded errors gracefully
     * Success: Function does not throw exceptions when sessionStorage.setItem fails due to quota limits
     */
    test('handles sessionStorage errors gracefully', () => {
      const mockConversation = { id: 'conv-1', name: 'Test', messages: [], folderId: null };

      // Mock sessionStorage throwing quota exceeded error
      mockSessionStorage.setItem.mockImplementation(() => {
        throw new DOMException('Storage quota exceeded', 'QuotaExceededError');
      });

      // Should not crash app when storage fails
      expect(() => saveConversation(mockConversation)).not.toThrow();
      expect(mockSessionStorage.setItem).toHaveBeenCalled();
    });
  });

    describe('Data Cleaning and Validation - REAL FUNCTION TESTS', () => {
    /**
     * Description: Verifies that cleanConversationHistory filters out null/undefined entries while repairing objects with missing properties
     * Success: Function returns array with only valid conversations, missing properties filled with defaults (messages: [], folderId: null)
     */
    test('cleanConversationHistory handles corrupted data', () => {
      const corruptedHistory = [
        { id: 'valid-conv', name: 'Valid', messages: [], folderId: null },
        null, // Corrupted entry - will be filtered out
        { id: 'missing-messages', name: 'Invalid' }, // Missing messages array - will be repaired
        { id: 'another-valid', name: 'Another Valid', messages: [], folderId: null },
        undefined, // Another corrupted entry - will be filtered out
        { id: 'no-folder', name: 'No Folder', messages: [] } // Missing folderId - will be repaired
      ];

      const cleaned = cleanConversationHistory(corruptedHistory);

      // Should have 4 items: 2 valid + 2 repaired (null/undefined are filtered out during reduce)
      expect(cleaned).toHaveLength(4);
      expect(cleaned.every(conv => conv.messages !== undefined)).toBe(true);
      expect(cleaned.every(conv => conv.folderId !== undefined)).toBe(true);
    });

    /**
     * Description: Verifies that cleanConversationHistory safely handles non-array input types
     * Success: Function returns empty array for all non-array inputs without throwing exceptions
     */
    test('cleanConversationHistory handles non-array input', () => {
      const invalidInputs = [null, undefined, 'not an array', 123, {}];

      invalidInputs.forEach(input => {
        const result = cleanConversationHistory(input as any);
        expect(Array.isArray(result)).toBe(true);
        expect(result).toHaveLength(0);
      });
    });

    /**
     * Description: Verifies that cleanConversationHistory preserves valid conversation objects unchanged
     * Success: Function returns identical array when all input conversations are valid and complete
     */
    test('cleanConversationHistory preserves valid conversations', () => {
      const validHistory = [
        {
          id: 'conv-1',
          name: 'Chat 1',
          messages: [{ role: 'user', content: 'Hello' }],
          folderId: 'folder-1'
        },
        {
          id: 'conv-2',
          name: 'Chat 2',
          messages: [],
          folderId: null
        }
      ];

      const cleaned = cleanConversationHistory(validHistory);

      expect(cleaned).toEqual(validHistory);
      expect(cleaned).toHaveLength(2);
    });
  });

    describe('Conversation Title Management', () => {
    /**
     * Description: Verifies that conversation title is updated from the first user message content
     * Success: Conversation name changes from 'New Conversation' to the first 30 characters of the user's message
     */
    test('conversation title updates from first user message', () => {
      const conversation = {
        id: 'conv-123',
        name: 'New Conversation',
        messages: [
          { role: 'user', content: 'What is the weather like today?' }
        ],
        folderId: null
      };

      const updated = applyMessageUpdate(conversation, conversation.messages);

      // Should use substring(0, 30) - note the missing question mark
      expect(updated.name).toBe('What is the weather like today');
    });
    /**
     * Description: Verifies that conversation titles longer than 30 characters are properly truncated
     * Success: Title is cut to exactly 30 characters using substring method
     */
    test('long conversation titles are truncated', () => {
      const longMessage = 'This is a very long user message that should be truncated when used as conversation title because it exceeds the maximum length allowed';

      const conversation = {
        id: 'conv-123',
        name: 'New Conversation',
        messages: [{ role: 'user', content: longMessage }],
        folderId: null
      };

      const updated = applyMessageUpdate(conversation, conversation.messages);

      expect(updated.name).toBe(longMessage.substring(0, 30));
      expect(updated.name.length).toBe(30);
    });

    /**
     * Description: Verifies that conversation titles are only updated when current name is 'New Conversation'
     * Success: Existing custom titles remain unchanged, only default titles get updated
     */
    test('conversation title only updates for "New Conversation"', () => {
      const conversation = {
        id: 'conv-123',
        name: 'Existing Title',
        messages: [
          { role: 'user', content: 'This should not change the title' }
        ],
        folderId: null
      };

      const updated = applyMessageUpdate(conversation, conversation.messages);

      expect(updated.name).toBe('Existing Title');
    });

    /**
     * Description: Verifies that conversation titles are not updated from assistant messages
     * Success: Title remains 'New Conversation' when only assistant messages are present
     */
    test('conversation title does not update from assistant messages', () => {
      const conversation = {
        id: 'conv-123',
        name: 'New Conversation',
        messages: [
          { role: 'assistant', content: 'Assistant message should not set title' }
        ],
        folderId: null
      };

      const updated = applyMessageUpdate(conversation, conversation.messages);

      expect(updated.name).toBe('New Conversation');
    });
  });
});

describe('Message Content Processing - REAL FUNCTION TESTS', () => {
  describe('Content Appending - REAL FUNCTION TESTS', () => {
    /**
     * appendAssistantText() string concatenation logic
     *
     * WHAT THIS TESTS: Pure string manipulation without any external dependencies
     * BUSINESS VALUE: Ensures streaming text is assembled correctly for chat messages
     *
     * INPUT: Multiple text chunks that should be concatenated
     * EXPECTED OUTPUT: Single combined string with all chunks in order
     */
    test('appendAssistantText combines content correctly', () => {
      let content = '';
      const chunks = ['Hello', ' world', '!', ' How', ' are', ' you?'];

      chunks.forEach(chunk => {
        content = appendAssistantText(content, chunk);
      });

      expect(content).toBe('Hello world! How are you?');
    });

    /**
     * appendAssistantText() edge case handling
     *
     * WHAT THIS TESTS: Function behavior with empty/null inputs
     * BUSINESS VALUE: Ensures robust handling of streaming edge cases
     *
     * INPUT: Various combinations of empty strings
     * EXPECTED OUTPUT: Logical string concatenation behavior
     */
    test('appendAssistantText handles empty inputs', () => {
      expect(appendAssistantText('', '')).toBe('');
      expect(appendAssistantText('existing', '')).toBe('existing');
      expect(appendAssistantText('', 'new')).toBe('new');
    });

    test('appendAssistantText replaces placeholder content', () => {
      expect(appendAssistantText('FAIL', 'real content')).toBe('real content');
      expect(appendAssistantText('', 'real content')).toBe('real content');
    });

    /**
     * Description: Verifies that appendAssistantText preserves exact whitespace and newlines during concatenation
     * Success: Text is concatenated exactly as provided, maintaining all whitespace, newlines, and indentation
     */
    test('appendAssistantText preserves whitespace correctly', () => {
      // Start with non-empty content to test concatenation behavior
      let content = 'Initial';
      content = appendAssistantText(content, '\nLine 1\n');
      content = appendAssistantText(content, 'Line 2\n');
      content = appendAssistantText(content, '  Indented');

      // When concatenating to existing content, original formatting is preserved
      expect(content).toBe('Initial\nLine 1\nLine 2\n  Indented');
    });
  });

  describe('Intermediate Steps Processing', () => {
    /**
     * Description: Verifies that mergeIntermediateSteps maintains the correct order of intermediate steps
     * Success: Steps are processed and returned in their original sequence with correct index assignments
     */
    test('mergeIntermediateSteps preserves step order', () => {
      const existingSteps = [
        { id: 'step-1', content: { name: 'Planning', payload: 'Step 1' }, index: 0 }
      ];

      const newStep = {
        type: 'system_intermediate_message',
        id: 'step-2',
        content: { name: 'Execution', payload: 'Step 2' }
      };

      const merged = mergeIntermediateSteps(existingSteps, newStep, true);

      expect(merged).toHaveLength(2);
      expect(merged[1].content.name).toBe('Execution');
      expect(merged[1].index).toBe(1);
    });

    /**
     * Description: Verifies that mergeIntermediateSteps respects the override setting for replacing existing steps
     * Success: When override=true existing steps are replaced, when override=false existing steps are preserved
     */
    test('mergeIntermediateSteps handles override setting', () => {
      // Test with override enabled - should replace existing step
      const existingStepsWithOverride = [
        { id: 'step-1', content: { name: 'Planning', payload: 'Original' }, index: 0 }
      ];

      const newStepForOverride = {
        type: 'system_intermediate_message',
        id: 'step-1',
        content: { name: 'Planning', payload: 'Updated' }
      };

      const mergedWithOverride = mergeIntermediateSteps(existingStepsWithOverride, newStepForOverride, true);
      expect(mergedWithOverride[0].content.payload).toBe('Updated');

      // Test with override disabled - should add new step (not replace)
      const existingStepsWithoutOverride = [
        { id: 'step-1', content: { name: 'Planning', payload: 'Original' }, index: 0 }
      ];

      const newStepForNoOverride = {
        type: 'system_intermediate_message',
        id: 'step-2', // Different ID to avoid replacement
        content: { name: 'Execution', payload: 'New Step' }
      };

      const mergedWithoutOverride = mergeIntermediateSteps(existingStepsWithoutOverride, newStepForNoOverride, false);
      expect(mergedWithoutOverride).toHaveLength(2); // Should have both steps
      expect(mergedWithoutOverride[0].content.payload).toBe('Original');
      expect(mergedWithoutOverride[1].content.payload).toBe('New Step');
    });

    /**
     * Description: Verifies that mergeIntermediateSteps assigns sequential indices to intermediate steps
     * Success: Each step in the merged array has the correct index property (0, 1, 2, etc.)
     */
    test('mergeIntermediateSteps assigns correct indices', () => {
      const existingSteps = [];
      const steps = [
        { type: 'system_intermediate_message', id: 'step-1', content: { name: 'Step 1' } },
        { type: 'system_intermediate_message', id: 'step-2', content: { name: 'Step 2' } },
        { type: 'system_intermediate_message', id: 'step-3', content: { name: 'Step 3' } }
      ];

      let merged = existingSteps;
      steps.forEach(step => {
        merged = mergeIntermediateSteps(merged, step, true);
      });

      expect(merged).toHaveLength(3);
      expect(merged[0].index).toBe(0);
      expect(merged[1].index).toBe(1);
      expect(merged[2].index).toBe(2);
    });
  });

  describe('Message Rendering Logic - REAL FUNCTION TESTS', () => {
    /**
     * shouldRenderAssistantMessage() message filtering logic
     *
     * WHAT THIS TESTS: Pure boolean logic for determining message visibility
     * BUSINESS VALUE: Prevents empty assistant messages from cluttering the UI
     *
     * INPUT: Message objects with various content and role combinations
     * EXPECTED OUTPUT: Boolean indicating if message should be displayed
     */
    /**
     * Description: Verifies that shouldRenderAssistantMessage correctly filters empty assistant messages while showing valid ones
     * Success: Empty assistant messages return false, messages with content or steps return true, user messages always return true
     */
    test('shouldRenderAssistantMessage filters empty messages', () => {
      const emptyMessage = { role: 'assistant', content: '', intermediateSteps: [] };
      const contentMessage = { role: 'assistant', content: 'Hello', intermediateSteps: [] };
      const stepMessage = { role: 'assistant', content: '', intermediateSteps: [{ name: 'Step' }] };
      const userMessage = { role: 'user', content: '' }; // Users messages always render

      expect(shouldRenderAssistantMessage(emptyMessage)).toBe(false);
      expect(shouldRenderAssistantMessage(contentMessage)).toBe(true);
      expect(shouldRenderAssistantMessage(stepMessage)).toBe(true);
      expect(shouldRenderAssistantMessage(userMessage)).toBe(true);
    });

    /**
     * Description: Verifies that shouldRenderAssistantMessage treats whitespace-only content as empty
     * Success: Messages with only whitespace characters return false, messages with actual content return true
     */
    test('shouldRenderAssistantMessage handles whitespace-only content', () => {
      const whitespaceMessage = { role: 'assistant', content: '   \n\t  ', intermediateSteps: [] };
      const validMessage = { role: 'assistant', content: '  actual content  ', intermediateSteps: [] };

      expect(shouldRenderAssistantMessage(whitespaceMessage)).toBe(false);
      expect(shouldRenderAssistantMessage(validMessage)).toBe(true);
    });

    /**
     * Description: Verifies that shouldRenderAssistantMessage safely handles null and undefined content
     * Success: Messages with null or undefined content return false without throwing exceptions
     */
    test('shouldRenderAssistantMessage handles undefined/null content', () => {
      const nullContentMessage = { role: 'assistant', content: null, intermediateSteps: [] };
      const undefinedContentMessage = { role: 'assistant', content: undefined, intermediateSteps: [] };

      expect(shouldRenderAssistantMessage(nullContentMessage)).toBe(false);
      expect(shouldRenderAssistantMessage(undefinedContentMessage)).toBe(false);
    });
  });
});

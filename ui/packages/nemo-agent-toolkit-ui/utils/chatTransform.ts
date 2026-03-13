/**
 * Pure transformation functions for chat message processing
 * These functions have no side effects and are easily testable
 */

import { Message, Conversation } from '@/types/chat';
import { 
  WebSocketInbound, 
  SystemResponseMessage, 
  SystemIntermediateMessage,
  IntermediateStep 
} from '@/types/websocket';
import { processIntermediateMessage } from '@/utils/app/helper';

/**
 * Determines if a WebSocket message should trigger content appending to assistant message
 * Only true for system_response_message with status=in_progress and non-empty text
 */
export function shouldAppendResponse(message: WebSocketInbound): boolean {
  if (message.type !== 'system_response_message') {
    return false;
  }
  
  const systemResponse = message as SystemResponseMessage;
  const text = systemResponse.content?.text;
  
  return (
    systemResponse.status === 'in_progress' &&
    Boolean(text && text.trim())
  );
}

/**
 * Safely appends new text to existing assistant content
 * Replaces empty/placeholder content, concatenates to existing content
 * Note: Preserves whitespace to maintain proper formatting between content chunks
 */
export function appendAssistantText(previousContent: string, newText: string): string {
  // Handle null/undefined inputs gracefully
  if (!previousContent) {
    previousContent = '';
  }
  if (!newText) {
    newText = '';
  }
  
  // Force string materialization to prevent race conditions
  void newText.length;
  
  const trimmedNew = newText.trim();
  const trimmedPrev = previousContent.trim();
  
  // If no new text (whitespace only), return previous
  if (!trimmedNew) {
    return previousContent;
  }
  
  // Replace empty string or placeholder content - preserve original newText with whitespace
  if (!trimmedPrev || trimmedPrev === 'FAIL') {
    return newText;  // Return original with whitespace preserved
  }
  
  // Concatenate to existing content - preserve all whitespace
  return previousContent + newText;
}

/**
 * Merges intermediate steps immutably, respecting override settings
 */
export function mergeIntermediateSteps(
  existingSteps: IntermediateStep[],
  incomingStep: SystemIntermediateMessage,
  intermediateStepOverride: boolean
): IntermediateStep[] {
  const stepWithIndex = {
    ...incomingStep,
    index: existingSteps.length || 0
  };
  
  return processIntermediateMessage(
    existingSteps,
    stepWithIndex,
    intermediateStepOverride
  );
}

/**
 * Immutably applies a message update to a conversation
 * Preserves conversation title update logic
 */
export function applyMessageUpdate(
  conversation: Conversation,
  updatedMessages: Message[]
): Conversation {
  let updatedConversation = {
    ...conversation,
    messages: updatedMessages
  };

  // Update conversation title if it's still "New Conversation"
  const firstUserMessage = updatedMessages.find((m) => m.role === 'user');
  if (
    firstUserMessage &&
    firstUserMessage.content &&
    updatedConversation.name === 'New Conversation'
  ) {
    updatedConversation = {
      ...updatedConversation,
      name: firstUserMessage.content.substring(0, 30)
    };
  }

  return updatedConversation;
}

/**
 * Creates a new assistant message immutably
 */
export function createAssistantMessage(
  id?: string,
  parentId?: string,
  content: string = '',
  intermediateSteps: IntermediateStep[] = [],
  humanInteractionMessages: any[] = [],
  errorMessages: any[] = []
): Message {
  return {
    role: 'assistant' as const,
    id,
    parentId,
    content,
    intermediateSteps,
    humanInteractionMessages,
    errorMessages,
    timestamp: Date.now()
  };
}

/**
 * Updates assistant message content immutably with proper content merging
 */
export function updateAssistantMessage(
  message: Message,
  newContent?: string,
  newIntermediateSteps?: IntermediateStep[]
): Message {
  return {
    ...message,
    content: newContent !== undefined ? newContent : message.content || '',
    intermediateSteps: newIntermediateSteps || message.intermediateSteps || [],
    timestamp: Date.now()
  };
}

/**
 * Determines if an assistant message should be rendered
 * Only render if it has content or intermediate steps
 */
export function shouldRenderAssistantMessage(message: Message): boolean {
  if (message.role !== 'assistant') {
    return true; // Always render non-assistant messages
  }
  
  const content = message.content;
  const hasContent = Boolean(content && content.trim());
  const hasIntermediateSteps = Boolean(message.intermediateSteps?.length);
  
  return hasContent || hasIntermediateSteps;
}

/**
 * Extracts the final content from a conversation for display
 */
export function extractConversationContent(conversation: Conversation): string {
  const lastMessage = conversation.messages[conversation.messages.length - 1];
  return lastMessage?.content || '';
}
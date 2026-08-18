// SPDX-License-Identifier: MIT
/**
 * Pure transforms over conversations and messages.
 *
 * The streaming path calls these on every frame, so they stay free of side
 * effects and of React: what a frame does to a conversation is decided here and
 * tested without mounting anything.
 */
import {
  CONVERSATION_TITLE_MAX_LENGTH,
  NEW_CONVERSATION_NAME,
  type Conversation,
  type Message,
} from '../types/chat';
import type {
  IntermediateStep,
  SystemIntermediateMessage,
  SystemResponseMessage,
  WebSocketInbound,
} from '../types/websocket';
import { processIntermediateMessage } from './intermediateSteps';

/** Placeholder some backends emit before real content arrives. */
const FAILED_PLACEHOLDER = 'FAIL';

/**
 * True when a frame carries assistant text to append.
 *
 * `complete` frames close the turn and repeat text already appended, so
 * appending them would duplicate the answer.
 */
export function shouldAppendResponse(message: WebSocketInbound): boolean {
  if (message.type !== 'system_response_message') return false;

  const response = message as SystemResponseMessage;
  return response.status === 'in_progress' && Boolean(response.content?.text?.trim());
}

/**
 * Joins a streamed fragment onto the answer so far.
 *
 * Whitespace inside a fragment is preserved — it carries markdown structure —
 * but a fragment that is only whitespace is dropped, and a placeholder is
 * replaced rather than prefixed.
 */
export function appendAssistantText(previousContent: string, newText: string): string {
  const previous = previousContent || '';
  const incoming = newText || '';

  if (!incoming.trim()) return previous;

  const trimmedPrevious = previous.trim();
  if (!trimmedPrevious || trimmedPrevious === FAILED_PLACEHOLDER) return incoming;

  return previous + incoming;
}

/**
 * Folds an incoming step into a message's step tree.
 *
 * New steps are indexed by current tree size, which is their render order.
 */
export function mergeIntermediateSteps(
  existingSteps: IntermediateStep[],
  incomingStep: SystemIntermediateMessage,
  intermediateStepOverride: boolean,
): IntermediateStep[] {
  const stepWithIndex = { ...incomingStep, index: existingSteps.length || 0 };

  return processIntermediateMessage(existingSteps, stepWithIndex, intermediateStepOverride);
}

/**
 * Returns a conversation with `updatedMessages` applied.
 *
 * An untitled conversation takes its name from the first user turn, which is
 * why this is not a plain spread at the call site.
 */
export function applyMessageUpdate(
  conversation: Conversation,
  updatedMessages: Message[],
): Conversation {
  const updated: Conversation = { ...conversation, messages: updatedMessages };

  if (updated.name !== NEW_CONVERSATION_NAME) return updated;

  const firstUserMessage = updatedMessages.find((message) => message.role === 'user');
  if (!firstUserMessage?.content) return updated;

  return {
    ...updated,
    name: firstUserMessage.content.substring(0, CONVERSATION_TITLE_MAX_LENGTH),
  };
}

/** Builds the empty assistant turn that streamed content fills in. */
export function createAssistantMessage(
  id?: string,
  parentId?: string,
  content = '',
  intermediateSteps: IntermediateStep[] = [],
  humanInteractionMessages: any[] = [],
  errorMessages: any[] = [],
): Message {
  return {
    role: 'assistant',
    id,
    parentId,
    content,
    intermediateSteps,
    humanInteractionMessages,
    errorMessages,
    timestamp: Date.now(),
  };
}

/**
 * Returns a copy of `message` with new content and/or steps.
 *
 * Omitted arguments keep their current value, so a step-only update does not
 * blank the text streamed so far.
 */
export function updateAssistantMessage(
  message: Message,
  newContent?: string,
  newIntermediateSteps?: IntermediateStep[],
): Message {
  return {
    ...message,
    content: newContent !== undefined ? newContent : message.content || '',
    intermediateSteps: newIntermediateSteps || message.intermediateSteps || [],
    timestamp: Date.now(),
  };
}

/**
 * True when a message is worth showing.
 *
 * An assistant turn is created empty the moment a request goes out; rendering
 * it before anything arrives would show a blank bubble under the user's turn.
 */
export function shouldRenderAssistantMessage(message: Message): boolean {
  if (message.role !== 'assistant') return true;

  return (
    Boolean(message.content?.trim()) || Boolean(message.intermediateSteps?.length)
  );
}

/** The latest message's content — what a conversation preview shows. */
export function extractConversationContent(conversation: Conversation): string {
  return conversation.messages.at(-1)?.content ?? '';
}

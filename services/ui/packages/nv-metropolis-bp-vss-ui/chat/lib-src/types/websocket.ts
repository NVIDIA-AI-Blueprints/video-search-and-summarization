// SPDX-License-Identifier: MIT
/**
 * Agent WebSocket protocol.
 *
 * Frames arrive from a backend we do not control, so every field is optional
 * until a guard proves otherwise. Narrowing happens here rather than at call
 * sites, which is what keeps a malformed frame from reaching the reducer.
 */

/** Fields any inbound frame may carry. */
export interface WebSocketMessageBase {
  id?: string;
  conversation_id?: string;
  parent_id?: string;
  timestamp?: string;
  status?: string;
}

export type SystemResponseStatus = 'in_progress' | 'complete';

/** A chunk of assistant text. `in_progress` frames append; `complete` closes the turn. */
export interface SystemResponseMessage extends WebSocketMessageBase {
  type: 'system_response_message';
  status: SystemResponseStatus;
  content?: { text?: string };
}

/** A step in the agent's reasoning, rendered as a collapsible entry. */
export interface SystemIntermediateMessage extends WebSocketMessageBase {
  type: 'system_intermediate_message';
  content?: { name?: string; payload?: string };
  index?: number;
  intermediate_steps?: IntermediateStep[];
}

/** A prompt requiring the user to act — today, OAuth consent. */
export interface SystemInteractionMessage extends WebSocketMessageBase {
  type: 'system_interaction_message';
  content?: {
    input_type?: string;
    oauth_url?: string;
    redirect_url?: string;
    text?: string;
  };
  thread_id?: string;
}

export interface ErrorMessage extends WebSocketMessageBase {
  type: 'error';
  content?: { text?: string; error?: string };
}

export type WebSocketInbound =
  | SystemResponseMessage
  | SystemIntermediateMessage
  | SystemInteractionMessage
  | ErrorMessage;

/** Steps nest arbitrarily; extra keys are backend-specific and preserved. */
export interface IntermediateStep {
  id?: string;
  parent_id?: string;
  index?: number;
  content?: any;
  intermediate_steps?: IntermediateStep[];
  [key: string]: any;
}

const INBOUND_TYPES = [
  'system_response_message',
  'system_intermediate_message',
  'system_interaction_message',
  'error',
] as const;

export function isSystemResponseMessage(message: any): message is SystemResponseMessage {
  return message?.type === 'system_response_message';
}

export function isSystemResponseInProgress(message: any): message is SystemResponseMessage {
  return isSystemResponseMessage(message) && message.status === 'in_progress';
}

export function isSystemResponseComplete(message: any): message is SystemResponseMessage {
  return isSystemResponseMessage(message) && message.status === 'complete';
}

export function isSystemIntermediateMessage(message: any): message is SystemIntermediateMessage {
  return message?.type === 'system_intermediate_message';
}

export function isSystemInteractionMessage(message: any): message is SystemInteractionMessage {
  return message?.type === 'system_interaction_message';
}

export function isErrorMessage(message: any): message is ErrorMessage {
  return message?.type === 'error';
}

export function isOAuthConsentMessage(message: any): message is SystemInteractionMessage {
  return isSystemInteractionMessage(message) && message.content?.input_type === 'oauth_consent';
}

/**
 * True when the frame names the conversation it belongs to.
 *
 * Without it a frame cannot be routed, and applying it to whatever is selected
 * would write one conversation's response into another.
 */
export function validateConversationId(message: any): boolean {
  if (!message || typeof message !== 'object') return false;

  return (
    typeof message.conversation_id === 'string' &&
    message.conversation_id.trim().length > 0
  );
}

/** True when the frame is one of the types we know how to handle. */
export function validateWebSocketMessage(message: any): message is WebSocketInbound {
  if (!message || typeof message !== 'object') return false;

  return (
    typeof message.type === 'string' &&
    (INBOUND_TYPES as readonly string[]).includes(message.type)
  );
}

/**
 * Strict validation for the receive path.
 *
 * Throws rather than returning false: an unroutable frame is a protocol bug,
 * and the message text is what makes it diagnosable from a user's console.
 *
 * @throws if the frame is not a known type, or names no conversation.
 */
export function validateWebSocketMessageWithConversationId(
  message: any,
): message is WebSocketInbound {
  if (!validateWebSocketMessage(message)) {
    throw new Error(
      `Invalid WebSocket message structure. Expected message with valid 'type' field, got: ${JSON.stringify(
        message,
      )}`,
    );
  }

  if (!validateConversationId(message)) {
    throw new Error(
      `WebSocket message missing required conversation_id. Message type: ${
        (message as WebSocketInbound).type
      }, message: ${JSON.stringify(message)}`,
    );
  }

  return true;
}

/**
 * The URL to send the user to for consent, or null if this frame is not an
 * OAuth prompt. Backends disagree on the field name, so all three are checked.
 */
export function extractOAuthUrl(message: SystemInteractionMessage): string | null {
  if (!isOAuthConsentMessage(message)) return null;

  return (
    message.content?.oauth_url ||
    message.content?.redirect_url ||
    message.content?.text ||
    null
  );
}

/** True when the frame carries assistant text to append to the open turn. */
export function shouldAppendResponseContent(message: WebSocketInbound): boolean {
  return (
    isSystemResponseInProgress(message) && Boolean(message.content?.text?.trim())
  );
}

/**
 * WebSocket message type definitions and type guards
 * Provides type safety for WebSocket message handling
 */

// Base interface for all WebSocket messages
export interface WebSocketMessageBase {
  id?: string;
  conversation_id?: string;
  parent_id?: string;
  timestamp?: string;
  status?: string;
}

// System response message types
export type SystemResponseStatus = 'in_progress' | 'complete';

export interface SystemResponseMessage extends WebSocketMessageBase {
  type: 'system_response_message';
  status: SystemResponseStatus;
  content?: { 
    text?: string;
  };
}

// Intermediate step message
export interface SystemIntermediateMessage extends WebSocketMessageBase {
  type: 'system_intermediate_message';
  content?: {
    name?: string;
    payload?: string;
  };
  index?: number;
  intermediate_steps?: IntermediateStep[];
}

// Human interaction message (OAuth, etc.)
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

// Error message
export interface ErrorMessage extends WebSocketMessageBase {
  type: 'error';
  content?: {
    text?: string;
    error?: string;
  };
}

// Union type for all WebSocket messages
export type WebSocketInbound = 
  | SystemResponseMessage 
  | SystemIntermediateMessage 
  | SystemInteractionMessage 
  | ErrorMessage;

// Intermediate step structure
export interface IntermediateStep {
  id?: string;
  parent_id?: string;
  index?: number;
  content?: any;
  intermediate_steps?: IntermediateStep[];
  [key: string]: any;
}

// Type guards for WebSocket messages
export function isSystemResponseMessage(message: any): message is SystemResponseMessage {
  return message?.type === 'system_response_message';
}

export function isSystemResponseInProgress(message: any): message is SystemResponseMessage {
  return (
    isSystemResponseMessage(message) && 
    message.status === 'in_progress'
  );
}

export function isSystemResponseComplete(message: any): message is SystemResponseMessage {
  return (
    isSystemResponseMessage(message) && 
    message.status === 'complete'
  );
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
  return (
    isSystemInteractionMessage(message) &&
    message.content?.input_type === 'oauth_consent'
  );
}

/**
 * Validates that a message has a valid conversation ID
 */
export function validateConversationId(message: any): boolean {
  if (!message || typeof message !== 'object') {
    return false;
  }
  
  // conversation_id must be present and be a non-empty string
  return (
    typeof message.conversation_id === 'string' && 
    message.conversation_id.trim().length > 0
  );
}

/**
 * Validates that a message has the minimum required structure
 */
export function validateWebSocketMessage(message: any): message is WebSocketInbound {
  if (!message || typeof message !== 'object') {
    return false;
  }
  
  return (
    typeof message.type === 'string' &&
    [
      'system_response_message',
      'system_intermediate_message', 
      'system_interaction_message',
      'error'
    ].includes(message.type)
  );
}

/**
 * Validates WebSocket message structure AND conversation ID presence
 * Throws descriptive errors for debugging
 */
export function validateWebSocketMessageWithConversationId(message: any): message is WebSocketInbound {
  // First check basic message structure
  if (!validateWebSocketMessage(message)) {
    throw new Error(
      `Invalid WebSocket message structure. Expected message with valid 'type' field, got: ${JSON.stringify(message)}`
    );
  }
  
  // Then check conversation ID
  if (!validateConversationId(message)) {
    throw new Error(
      `WebSocket message missing required conversation_id. Message type: ${message.type}, message: ${JSON.stringify(message)}`
    );
  }
  
  return true;
}

/**
 * Extracts OAuth URL from interaction message safely
 */
export function extractOAuthUrl(message: SystemInteractionMessage): string | null {
  if (!isOAuthConsentMessage(message)) {
    return null;
  }
  
  return (
    message.content?.oauth_url ||
    message.content?.redirect_url ||
    message.content?.text ||
    null
  );
}

/**
 * Determines if a response should append content (type guards + content check)
 */
export function shouldAppendResponseContent(message: WebSocketInbound): boolean {
  return (
    isSystemResponseInProgress(message) &&
    Boolean(message.content?.text?.trim())
  );
}
// SPDX-License-Identifier: MIT
/**
 * @nv-metropolis-bp-vss-ui/chat
 *
 * VSS-owned agent chat. Replaces the vendored `@nemo-agent-toolkit/ui` chat so
 * the UI is not coupled to a single agent toolkit and carries no third-party
 * UI source. Backends are selected by protocol (OpenAI-compatible HTTP/SSE,
 * WebSocket, OpenClaw gateway) rather than by vendor.
 *
 * Behaviour is specified by the test suite under `__tests__/`, ported from the
 * outgoing implementation so parity is verifiable rather than assumed.
 */

// Query lifecycle
export {
  isQueryProcessing,
  isActiveConversationProcessing,
  isConversationQueryInFlight,
  isFolderDeleteBlocked,
} from './utils/queryProcessing';
export type { ProcessingScopedConversation } from './utils/queryProcessing';

// Upload auto-prompt gating
export {
  shouldSendUploadHiddenMessage,
  shouldAllowChatMessageSend,
  stripUploadConversationScope,
  countAllowedUploadHiddenPrompts,
} from './utils/uploadHiddenMessage';
export type {
  UploadScopedMessage,
  ChatMessageSendCheck,
  UploadBatchCompletion,
} from './utils/uploadHiddenMessage';

// Media
export { isValidMediaURL } from './utils/media/validation';
export { downloadImageFromUrl } from './utils/media/download';

// Security
export { isValidConsentPromptURL } from './utils/security/oauth-validation';
export { validateProxyHttpPath } from './utils/security/url-validation';
export type { PathValidationResult } from './utils/security/url-validation';

// Conversation model
export type {
  CallerInfo,
  ChatBody,
  Conversation,
  CustomAgentParams,
  Message,
  QueryDataContext,
  Role,
} from './types/chat';
export { CONVERSATION_TITLE_MAX_LENGTH, NEW_CONVERSATION_NAME } from './types/chat';

// Agent WebSocket protocol
export {
  extractOAuthUrl,
  isErrorMessage,
  isOAuthConsentMessage,
  isSystemInteractionMessage,
  isSystemIntermediateMessage,
  isSystemResponseComplete,
  isSystemResponseInProgress,
  isSystemResponseMessage,
  shouldAppendResponseContent,
  validateConversationId,
  validateWebSocketMessage,
  validateWebSocketMessageWithConversationId,
} from './types/websocket';
export type {
  ErrorMessage,
  IntermediateStep,
  SystemInteractionMessage,
  SystemIntermediateMessage,
  SystemResponseMessage,
  SystemResponseStatus,
  WebSocketInbound,
  WebSocketMessageBase,
} from './types/websocket';

// Conversation transforms
export {
  appendAssistantText,
  applyMessageUpdate,
  createAssistantMessage,
  extractConversationContent,
  mergeIntermediateSteps,
  shouldAppendResponse,
  shouldRenderAssistantMessage,
  updateAssistantMessage,
} from './utils/chatTransform';
export { processIntermediateMessage } from './utils/intermediateSteps';

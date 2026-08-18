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

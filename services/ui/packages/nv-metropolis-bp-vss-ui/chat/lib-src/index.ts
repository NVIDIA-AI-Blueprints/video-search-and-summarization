// SPDX-License-Identifier: MIT
/**
 * VSS chat interface.
 *
 * Replaces the NeMo Agent Toolkit chat UI for the chat tab and the docked
 * sidebar. Depends on no toolkit code: it speaks the BYO agent contract
 * (OpenAI-shaped request, SSE response) directly, so any backend implementing
 * `/chat/stream` works, including the VSS agent adapter driving OpenClaw or
 * Hermes.
 */
export { ChatPanel, default as default } from './ChatPanel';
export { ConversationList } from './ConversationList';
export { ChatSteps } from './ChatSteps';
export { InteractionModal } from './InteractionModal';
export { ChatUpload } from './ChatUpload';
export { AgentParams, fieldsToParams, parseParamsJson, useParamFields } from './AgentParams';

export { useChatStream, buildContextPrefix } from './useChatStream';
export type { UseChatStreamResult, UseChatStreamOptions, SendOptions } from './useChatStream';
export { useConversations } from './useConversations';
export type { UseConversationsResult } from './useConversations';

export { SseParser, extractContent, buildStepTree } from './sse';
export type { SseEvent, InteractionRequest } from './sse';

export {
  buildExport,
  createConversation,
  filterConversations,
  parseImport,
  sanitizeForPersistence,
  titleFromMessage,
} from './conversations';
export type { ChatExportV4, ImportResult } from './conversations';

export {
  clearAllConversations,
  initConversationSessionLifecycle,
  loadConversations,
  saveConversations,
} from './storage';

export { getMarkdownComponents } from './markdown/components';
export { fixMalformedHtml } from './markdown/streaming';

export type {
  CallerInfo,
  ChatAnswerHandler,
  ChatAttachment,
  ChatEndpointConfig,
  ChatFeatureFlags,
  ChatMessage,
  ChatPanelProps,
  ChatRole,
  ChatSidebarControlHandlers,
  ChatStep,
  ChatVideoUploadCompletePayload,
  Conversation,
  CustomAgentParamsValues,
  ParamField,
  ParamFieldConfig,
  ParamType,
  QueryDataContext,
} from './types';

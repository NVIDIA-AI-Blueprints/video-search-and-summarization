// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * VSS chat interface.
 *
 * Replaces the NeMo Agent Toolkit chat UI for the chat tab and docked sidebar.
 * It depends on no toolkit code and consumes the backend-neutral VSS agent
 * agent API contract directly, with legacy chat-SSE compatibility.
 */
export { ChatPanel, default as default } from './ChatPanel';
export { ConversationList } from './ConversationList';
export { ChatSteps } from './ChatSteps';
export { ChatUpload } from './ChatUpload';
export { AgentParams, fieldsToParams, parseParamsJson, useParamFields } from './AgentParams';

export { useChatStream, buildContextPrefix } from './useChatStream';
export type { UseChatStreamResult, UseChatStreamOptions, SendOptions } from './useChatStream';
export { useConversations } from './useConversations';
export type { UseConversationsResult } from './useConversations';

export { SseParser, extractContent, buildStepTree } from './sse';
export type { SseEvent } from './sse';

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

export { useChatVideoUploadCompleteSubscription } from './uploadSubscription';
export type { RegisterChatVideoUploadComplete } from './uploadSubscription';

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

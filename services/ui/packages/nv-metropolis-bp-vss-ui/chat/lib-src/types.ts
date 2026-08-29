// SPDX-License-Identifier: MIT
/**
 * Types for the VSS chat interface.
 *
 * This package deliberately has no dependency on the NeMo Agent Toolkit UI.
 * It speaks the BYO agent contract directly: an OpenAI-shaped request in,
 * Server-Sent Events out. Any backend implementing that contract works here.
 *
 * The shapes below mirror the toolkit's `types/chat.ts` closely enough that a
 * feature ported from there behaves the same, without importing it.
 */

import type { FileUploadResult } from 'common';

export type ChatRole = 'user' | 'assistant';

/**
 * One tool/skill step reported by the agent while it works.
 *
 * `children` makes this a tree: the toolkit nests steps by `parent_id` and
 * renders the result as a `<details>` cascade. We keep the tree structured and
 * render it as React instead of serialising to HTML and re-parsing it, which is
 * where the toolkit's version leaks (half-written tags mid-stream).
 */
export interface ChatStep {
  id: string;
  name: string;
  status: 'in_progress' | 'complete' | 'error';
  /** Raw detail, shown only when the step is expanded. */
  payload?: string;
  index: number;
  parentId?: string;
  children?: ChatStep[];
}

/** Parent-provided renderable HTML shown under an assistant response. */
export type CallerInfo = string;

export interface ChatAttachment {
  /** data: URL. Images only, matching the toolkit. */
  content: string;
  type: 'image';
  name?: string;
}

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  /** Populated for assistant messages that reported tool activity. */
  steps?: ChatStep[];
  /** True while tokens are still arriving. */
  streaming?: boolean;
  error?: string;
  attachments?: ChatAttachment[];
  /** Sent to the agent but never rendered (upload auto-prompts). */
  hidden?: boolean;
  /**
   * Conversation active when an upload batch started. Used to drop a stale
   * auto-prompt if the user switched conversations mid-upload.
   */
  uploadConversationId?: string;
  /** HTML card rendered under an assistant answer, supplied by the embedder. */
  callerInfo?: CallerInfo;
  timestamp?: number;
}

/** A named thread of messages. Mirrors the toolkit's `Conversation`. */
export interface Conversation {
  id: string;
  name: string;
  messages: ChatMessage[];
}

/**
 * UI-only chip attached to the next message.
 *
 * `contextType` drives the chip icon and is never sent to the backend — only
 * `data` is, inside the `[Context: …]` prefix. Same contract as the toolkit's
 * `QueryDataContext`, so the Search tab's existing `addChatQueryContext` calls
 * work unchanged.
 */
export interface QueryDataContext {
  id: string;
  label: string;
  contextType: string;
  data: Record<string, unknown>;
}

/** Everything the panel needs to reach a backend. */
export interface ChatEndpointConfig {
  /** e.g. /api/vss-chat?surface=sidebar */
  url: string;
  /** Sent as Conversation-Id; the adapter maps it to one agent session. */
  conversationId: string;
  /** Merged into the request body (the UI's custom agent params). */
  extraParams?: Record<string, string | number | boolean>;
  headers?: Record<string, string>;
  /** Base URL for chunked video upload; enables the upload button when set. */
  uploadUrlBase?: string;
}

/**
 * Called with each completed assistant answer.
 *
 * This is how feature tabs (search, alerts) receive results without the chat
 * package knowing anything about them. Returning a string renders it as the
 * message's caller-info card, matching the toolkit's `onAnswerCompleteWithContent`.
 */
export type ChatAnswerHandler = (
  answer: string,
  /** Scopes any per-conversation artifact the consumer fetches for this answer. */
  conversationId: string,
) => CallerInfo | boolean | void;

/**
 * Payload for a finished upload batch.
 *
 * `FileUploadResult` is re-used from `common` rather than widened to a plain
 * record: the app's existing upload-complete handlers are typed against it, and
 * a looser type here would fail to assign at the call site.
 */
export interface ChatVideoUploadCompletePayload {
  results: { filename: string; result: FileUploadResult }[];
}

/** Handlers handed to an embedder that renders conversation controls itself. */
export interface ChatSidebarControlHandlers {
  conversations: Conversation[];
  filteredConversations: Conversation[];
  selectedConversationId: string | null;
  searchTerm: string;
  onSearchTermChange: (term: string) => void;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
  onRenameConversation: (id: string, name: string) => void;
  onDeleteConversation: (id: string) => void;
  onClearConversations: () => void;
  onExportData: () => void;
  onImportConversations: (data: unknown) => void;
  /** True while a turn is in flight; controls are disabled. */
  busy: boolean;
}

/** A field in the agent-parameters panel, parsed from the deployment's JSON. */
export type ParamType = 'string' | 'number' | 'boolean' | 'select';

export interface ParamFieldConfig {
  name: string;
  label: string;
  type: ParamType;
  'default-value': string | number | boolean;
  options?: string[];
  /** false = shown but read-only. */
  changeable?: boolean;
  'tooltip-info'?: string;
}

export interface ParamField extends ParamFieldConfig {
  id: string;
  value: string | number | boolean;
}

export type CustomAgentParamsValues = Record<string, string | number | boolean>;

/** Feature switches, mirroring the toolkit's NEXT_PUBLIC_CHAT_* env flags. */
export interface ChatFeatureFlags {
  /** Send the whole thread rather than just the latest turn. */
  chatHistory?: boolean;
  /** Show the tool-step disclosure. */
  intermediateSteps?: boolean;
  /** Expand intermediate steps by default. */
  expandIntermediateSteps?: boolean;
  messageCopy?: boolean;
  messageEdit?: boolean;
  messageSpeaker?: boolean;
  /** Show the mic button (browser SpeechRecognition). */
  inputMic?: boolean;
  /** Show the video upload button and welcome drop zone. */
  uploadFile?: boolean;
  /** Collect per-file metadata in the upload dialog. */
  uploadFileMetadata?: boolean;
  /** Show the theme toggle in the header menu. */
  themeToggle?: boolean;
  /** Show the conversation controls the panel owns (header menu). */
  headerMenu?: boolean;
}

export interface ChatPanelProps {
  endpoint: Omit<ChatEndpointConfig, 'conversationId'> & {
    conversationId?: string;
  };
  title?: string;
  /** Light surface against a dark app, or vice versa. */
  theme?: 'light' | 'dark';
  onThemeChange?: (theme: 'light' | 'dark') => void;
  placeholder?: string;
  /** @deprecated use `features.intermediateSteps` */
  showSteps?: boolean;
  features?: ChatFeatureFlags;
  /** JSON describing the agent-parameter fields; from NEXT_PUBLIC_CHAT_API_CUSTOM_AGENT_PARAMS_JSON. */
  customAgentParamsJson?: string;
  /** JSON template for the upload dialog's metadata fields. */
  uploadConfigTemplateJson?: string;
  /** Auto-prompt sent after a successful upload; `{filenames}` is substituted. */
  uploadHiddenMessageTemplate?: string;
  /**
   * Separates this instance's persisted conversations from another's, exactly
   * as the toolkit's prop of the same name does.
   */
  storageKeyPrefix?: string;
  /** False while the surface is hidden; suppresses autoscroll work. */
  isActive?: boolean;

  /** Notified with each finished assistant answer. */
  onAnswer?: ChatAnswerHandler;
  /** Notified when a turn finishes, without the content. */
  onAnswerComplete?: () => void;
  /** Notified when the user sends, so tabs can clear stale results. */
  onSubmit?: (message: string) => void;
  /** Receives a function the embedder can call to submit a message itself. */
  onSubmitMessageReady?: (submit: (message: string) => void) => void;
  /** Notified when a message was submitted programmatically. */
  onMessageSubmitted?: () => void;
  /** Receives a function the embedder can call to add a context chip. */
  onAddQueryContextReady?: (add: (item: QueryDataContext) => void) => void;
  /** Notified when an upload batch finishes with at least one success. */
  onChatVideoUploadComplete?: (payload: ChatVideoUploadCompletePayload) => void;
  /** Notified whenever a turn starts or ends. */
  onBusyChange?: (busy: boolean) => void;
  /** Receives conversation controls so an external sidebar can render them. */
  onControlsReady?: (handlers: ChatSidebarControlHandlers) => void;

  className?: string;
}

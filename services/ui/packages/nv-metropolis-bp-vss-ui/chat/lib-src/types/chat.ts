// SPDX-License-Identifier: MIT
/**
 * Conversation and message model.
 */
import type { CallerInfo } from 'common';

export type { CallerInfo };

export type Role = 'assistant' | 'user' | 'agent' | 'system';

/**
 * A tab-supplied reference attached to a turn — a video, an incident, a stream.
 * `data` is what reaches the backend; `contextType` and `label` are for display.
 */
export interface QueryDataContext {
  id: string;
  label: string;
  /** UI-only grouping (e.g. media/video). Not forwarded to the backend. */
  contextType: string;
  data: Record<string, unknown>;
}

export interface Message {
  id?: string;
  role: Role;
  content: string;
  intermediateSteps?: any;
  humanInteractionMessages?: any;
  errorMessages?: any;
  timestamp?: number;
  parentId?: string;
  /** Host-supplied HTML rendered beneath an assistant response. */
  callerInfo?: CallerInfo;
  /** Sent to the agent but not shown — used for upload auto-prompts. */
  hidden?: boolean;
  /**
   * Conversation active when an upload batch started, so a stale auto-prompt
   * can be dropped if the user switched away. Stripped before persistence.
   */
  uploadConversationId?: string;
}

export interface Conversation {
  id: string;
  name: string;
  messages: Message[];
  folderId: string | null;
  /** Set before the first turn, while the conversation is not yet persisted. */
  isHomepageConversation?: boolean;
  /** True while a query runs for this conversation in the background. */
  isQueryInFlight?: boolean;
}

/** Agent-specific parameters forwarded verbatim in the request body. */
export type CustomAgentParams = Record<string, string | number | boolean>;

export interface ChatBody {
  chatCompletionURL?: string;
  messages?: Message[];
  additionalProps?: any;
  [key: string]: any;
}

/** Title shown until the first user turn supplies one. */
export const NEW_CONVERSATION_NAME = 'New Conversation';

/** Conversation titles are derived from the first user turn, clipped to this. */
export const CONVERSATION_TITLE_MAX_LENGTH = 30;

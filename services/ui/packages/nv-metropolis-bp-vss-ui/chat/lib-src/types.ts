// SPDX-License-Identifier: MIT
/**
 * Types for the VSS chat interface.
 *
 * This package deliberately has no dependency on the NeMo Agent Toolkit UI.
 * It speaks the BYO agent contract directly: an OpenAI-shaped request in,
 * Server-Sent Events out. Any backend implementing that contract works here.
 */

export type ChatRole = 'user' | 'assistant';

/** One tool/skill step reported by the agent while it works. */
export interface ChatStep {
  id: string;
  name: string;
  status: 'in_progress' | 'complete' | 'error';
  /** Raw detail, shown only when the step is expanded. */
  payload?: string;
  index: number;
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
}

/** Everything the panel needs to reach a backend. */
export interface ChatEndpointConfig {
  /** e.g. http://172.19.0.1:9098/chat/stream */
  url: string;
  /** Sent as Conversation-Id; the adapter maps it to one agent session. */
  conversationId: string;
  /** Merged into the request body (the UI's custom agent params). */
  extraParams?: Record<string, string | number | boolean>;
  headers?: Record<string, string>;
}

/**
 * Called with each completed assistant answer.
 *
 * This is how feature tabs (search, alerts) receive results without the chat
 * package knowing anything about them. Returning true means the handler
 * consumed the answer.
 */
export type ChatAnswerHandler = (
  answer: string,
  /** Scopes any per-conversation artifact the consumer fetches for this answer. */
  conversationId: string,
) => boolean | void;

export interface ChatPanelProps {
  endpoint: Omit<ChatEndpointConfig, 'conversationId'> & {
    conversationId?: string;
  };
  title?: string;
  /** Light surface against a dark app, or vice versa. */
  theme?: 'light' | 'dark';
  placeholder?: string;
  /** Show the tool-step disclosure. */
  showSteps?: boolean;
  /** Notified with each finished assistant answer. */
  onAnswer?: ChatAnswerHandler;
  /** Notified when the user sends, so tabs can clear stale results. */
  onSubmit?: (message: string) => void;
  className?: string;
}

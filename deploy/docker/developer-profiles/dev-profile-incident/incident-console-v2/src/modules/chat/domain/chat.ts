// SPDX-License-Identifier: Apache-2.0

// IncidentChat (MVP2): multi-turn Q&A with cited evidence. These types are the
// vss-agent's POST /api/v1/incidents/chat contract (chat-contract-v1,
// services/agent/src/vss_agents/api/incident_chat.py ChatRequest / ChatResponse)
// in camelCase; modules/chat/infrastructure/agent/agent-chat.ts maps the snake_case.

import type { EvidenceItemType } from '@/modules/search/domain/search';

export type ChatRole = 'user' | 'assistant' | 'system';

export interface ChatMessage {
  role: ChatRole;
  /** At least 1 character. */
  content: string;
}

export interface ChatContext {
  /** Default global. */
  scope?: 'global' | 'incident';
  /** The incident to focus on when scope is incident. */
  incidentId?: string | null;
  evidenceCropIds?: string[] | null;
}

export interface ChatRequest {
  /** 1 to 128 characters; the agent generates one when absent. */
  sessionId?: string;
  /** At least one; the last user message is the query. */
  messages: ChatMessage[];
  context?: ChatContext;
  /** The saved report version (report_revisions.id) the question is about; citations come from it. */
  versionId?: number;
  /** An unsaved incident report edited from versionId; answers are grounded in it. Requires versionId. */
  draftReport?: Record<string, unknown>;
}

export interface ChatCitation {
  incidentId: string;
  itemId: string;
  itemType: EvidenceItemType;
  /** 0 or more, for jump-to-clip. */
  timestampSeconds: number;
  /** Signed by the agent; used as given. */
  cropImageUrl: string;
}

/** chat-contract-v2 (agent INCIDENT_CHAT_MODE=grounded): which report the answer is grounded in. */
export interface ChatGrounding {
  mode: 'grounded';
  scope: 'incident' | 'library';
  model: string;
  /** The report version answered from; null for a library question. */
  versionId: number | null;
  official: boolean | null;
  draft: boolean;
  /** Item ids the model cited that the report does not hold (never shown as citations). */
  droppedCitations: string[];
}

export interface ChatResponse {
  sessionId: string;
  message: {
    role: 'assistant';
    content: string;
    citations: ChatCitation[];
    /** chat-contract-v2: the report cannot answer (or contradicts the question); citations are then empty. */
    insufficientEvidence?: boolean;
  };
  grounding?: ChatGrounding;
}

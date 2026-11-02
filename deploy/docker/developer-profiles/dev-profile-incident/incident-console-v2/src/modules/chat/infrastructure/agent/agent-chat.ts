// SPDX-License-Identifier: Apache-2.0

// Adapter for the vss-agent's POST /api/v1/incidents/chat (chat-contract-v1):
// camelCase ChatRequest in, camelCase ChatResponse out.

import { readUpstream } from '@/lib/http';
import type { ChatCitation, ChatRequest, ChatResponse } from '@/modules/chat/domain/chat';

export function toAgentChatBody(request: ChatRequest): Record<string, unknown> {
  const { sessionId, messages, context, versionId, draftReport } = request;
  return {
    ...(sessionId ? { session_id: sessionId } : {}),
    messages: messages.map(({ role, content }) => ({ role, content })),
    ...(context
      ? {
          context: {
            ...(context.scope ? { scope: context.scope } : {}),
            ...(context.incidentId != null ? { incident_id: context.incidentId } : {}),
            ...(context.evidenceCropIds != null ? { evidence_crop_ids: context.evidenceCropIds } : {}),
          },
        }
      : {}),
    ...(versionId != null ? { version_id: versionId } : {}),
    ...(draftReport != null ? { draft_report: draftReport } : {}),
  };
}

type AgentCitation = {
  incident_id: string;
  item_id: string;
  item_type: ChatCitation['itemType'];
  timestamp_seconds: number;
  crop_image_url: string;
};
type AgentGrounding = {
  mode?: string;
  scope?: string;
  model?: string;
  version_id?: number | null;
  official?: boolean | null;
  draft?: boolean;
  dropped_citations?: string[];
  ids_resolved?: string[];
  incident_titles?: Record<string, unknown>;
  item_names?: Record<string, unknown>;
  versions?: Record<string, unknown>;
  candidates?: unknown[];
  answered_by?: string;
  count_exact?: boolean;
};

const stringEntries = (value: Record<string, unknown> | undefined): Record<string, string> =>
  Object.fromEntries(Object.entries(value ?? {}).filter((entry): entry is [string, string] => typeof entry[1] === 'string'));
type AgentChatResponse = {
  session_id?: string;
  message?: { role?: string; content?: string; citations?: AgentCitation[]; insufficient_evidence?: boolean };
  grounding?: AgentGrounding;
};

function groundingFrom(grounding: AgentGrounding | undefined): ChatResponse['grounding'] {
  if (grounding?.mode !== 'grounded' || (grounding.scope !== 'incident' && grounding.scope !== 'library')) return undefined;
  return {
    mode: 'grounded',
    scope: grounding.scope,
    model: String(grounding.model ?? ''),
    versionId: typeof grounding.version_id === 'number' ? grounding.version_id : null,
    official: typeof grounding.official === 'boolean' ? grounding.official : null,
    draft: grounding.draft === true,
    droppedCitations: Array.isArray(grounding.dropped_citations) ? grounding.dropped_citations.map(String) : [],
    idsResolved: Array.isArray(grounding.ids_resolved) ? grounding.ids_resolved.map(String) : [],
    incidentTitles: stringEntries(grounding.incident_titles),
    itemNames: stringEntries(grounding.item_names),
    versions: Object.fromEntries(
      Object.entries(grounding.versions ?? {}).filter(
        (entry): entry is [string, number] => typeof entry[1] === 'number' && Number.isInteger(entry[1]) && entry[1] > 0,
      ),
    ),
    candidates: Array.isArray(grounding.candidates) ? grounding.candidates.map(String) : [],
    answeredBy: grounding.answered_by === 'model' || grounding.answered_by === 'code' ? grounding.answered_by : null,
    countExact: typeof grounding.count_exact === 'boolean' ? grounding.count_exact : null,
  };
}

export function fromAgentChatResponse(payload: unknown): ChatResponse {
  const { session_id: sessionId, message, grounding } = (payload ?? {}) as AgentChatResponse;
  if (!sessionId || message?.role !== 'assistant' || typeof message.content !== 'string' || !Array.isArray(message.citations)) {
    throw new Error('Agent returned an invalid chat response');
  }
  return {
    sessionId,
    message: {
      role: 'assistant',
      content: message.content,
      citations: message.citations.map((c) => ({
        incidentId: c.incident_id,
        itemId: c.item_id,
        itemType: c.item_type,
        timestampSeconds: c.timestamp_seconds,
        cropImageUrl: c.crop_image_url,
      })),
      ...(typeof message.insufficient_evidence === 'boolean' ? { insufficientEvidence: message.insufficient_evidence } : {}),
    },
    ...(groundingFrom(grounding) ? { grounding: groundingFrom(grounding) } : {}),
  };
}

export async function sendAgentChat(agentUrl: string, request: ChatRequest, signal?: AbortSignal): Promise<ChatResponse> {
  const response = await fetch(`${agentUrl.replace(/\/$/, '')}/api/v1/incidents/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(toAgentChatBody(request)),
    signal,
  });
  return fromAgentChatResponse(await readUpstream(response, 'Agent /api/v1/incidents/chat'));
}

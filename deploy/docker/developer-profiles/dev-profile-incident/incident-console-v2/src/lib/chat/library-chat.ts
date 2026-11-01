// SPDX-License-Identifier: Apache-2.0

// What the library page's chat asks and how it shows the answer. Browser-safe.
// A library question names no incident and no version: the agent plans a search
// over every incident's report (its official version, else its newest) and cites
// items from several incidents.

import type { ChatCitation } from '@/modules/chat/domain/chat';

/** The body of POST /api/chat for one library question: no incidentId, so the agent answers in library scope. */
export function libraryChatRequestBody({
  messages,
  sessionId,
}: {
  messages: Array<{ role: 'user' | 'assistant'; content: string }>;
  sessionId?: string;
}) {
  return { messages, ...(sessionId ? { sessionId } : {}) };
}

export interface IncidentCitations {
  incidentId: string;
  citations: ChatCitation[];
}

/** An answer's citations grouped by incident, in the order the answer first cites each incident. */
export function citationsByIncident(citations: ChatCitation[]): IncidentCitations[] {
  const groups = new Map<string, ChatCitation[]>();
  for (const citation of citations) {
    const group = groups.get(citation.incidentId);
    if (group) group.push(citation);
    else groups.set(citation.incidentId, [citation]);
  }
  return Array.from(groups, ([incidentId, items]) => ({ incidentId, citations: items }));
}

/** The incident's report page, opened at the cited second (the page's `t` start parameter). */
export function citationHref(citation: Pick<ChatCitation, 'incidentId' | 'timestampSeconds'>): string {
  const base = `/reports/${encodeURIComponent(citation.incidentId)}`;
  return Number.isFinite(citation.timestampSeconds) && citation.timestampSeconds > 0
    ? `${base}?t=${Math.floor(citation.timestampSeconds)}`
    : base;
}

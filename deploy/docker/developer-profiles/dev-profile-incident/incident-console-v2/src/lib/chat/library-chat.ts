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

/**
 * The incident's report page at the version the answer read (`revision`, when known: the official version, else the
 * newest, may change later), opened at the cited second (the page's `t` start parameter).
 */
export function citationHref(citation: Pick<ChatCitation, 'incidentId' | 'timestampSeconds'>, versionId?: number | null): string {
  const query = new URLSearchParams();
  if (typeof versionId === 'number' && Number.isInteger(versionId) && versionId > 0) query.set('revision', String(versionId));
  if (Number.isFinite(citation.timestampSeconds) && citation.timestampSeconds > 0) {
    query.set('t', String(Math.floor(citation.timestampSeconds)));
  }
  const base = `/reports/${encodeURIComponent(citation.incidentId)}`;
  const search = query.toString();
  return search ? `${base}?${search}` : base;
}

/** A cited item's label: the name the answer gave it ("The glass bottle"), else its kind; never its id. */
export function citationLabel(citation: Pick<ChatCitation, 'incidentId' | 'itemId' | 'itemType'>, itemNames?: Record<string, string>) {
  const name = itemNames?.[`${citation.incidentId}/${citation.itemId}`] ?? citation.itemType;
  return name.charAt(0).toUpperCase() + name.slice(1);
}

/**
 * The reports a count or breakdown answer is about, when it cites no item: each matching incident shown, in rank
 * order, with its link at the version read. None for a refusal or an answer that cites items.
 */
export function matchingReports(
  message: { citations?: ChatCitation[]; insufficientEvidence?: boolean },
  grounding: { candidates?: string[]; versions?: Record<string, number>; answeredBy?: string | null } | undefined,
): Array<{ incidentId: string; href: string }> {
  if (message.insufficientEvidence || message.citations?.length || grounding?.answeredBy !== 'code') return [];
  return (grounding.candidates ?? []).map((incidentId) => ({
    incidentId,
    href: citationHref({ incidentId, timestampSeconds: 0 }, grounding.versions?.[incidentId]),
  }));
}

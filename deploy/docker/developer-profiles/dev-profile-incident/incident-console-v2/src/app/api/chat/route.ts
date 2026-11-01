// SPDX-License-Identifier: Apache-2.0

import { NextResponse } from 'next/server';
import { sendAgentChat } from '@/modules/chat/infrastructure/agent/agent-chat';
import { getServiceConfiguration } from '@/lib/env';
import { errorResponse, UpstreamError } from '@/lib/http';
import type { ChatMessage } from '@/modules/chat/domain/chat';

export const maxDuration = 120;
export async function POST(request: Request) {
  try {
    const input = (await request.json()) as {
      messages?: ChatMessage[];
      sessionId?: string;
      incidentId?: string;
      evidenceCropIds?: string[];
      versionId?: number;
      draftReport?: Record<string, unknown>;
    };
    if (!input.messages?.length) return NextResponse.json({ error: 'At least one message is required' }, { status: 400 });
    if (input.versionId != null && !(Number.isInteger(input.versionId) && input.versionId >= 1))
      return NextResponse.json({ error: 'versionId must be a positive integer' }, { status: 400 });
    if (input.draftReport != null && input.versionId == null)
      return NextResponse.json({ error: 'A draft report needs the versionId it was edited from' }, { status: 400 });
    const config = getServiceConfiguration();
    if (!config.agentUrl) throw new Error('INCIDENT_AGENT_BASE_URL is not configured');
    const result = await sendAgentChat(
      config.agentUrl,
      {
        messages: input.messages,
        ...(input.sessionId ? { sessionId: input.sessionId } : {}),
        ...(input.incidentId
          ? {
              context: {
                scope: 'incident' as const,
                incidentId: input.incidentId,
                ...(input.evidenceCropIds?.length ? { evidenceCropIds: input.evidenceCropIds } : {}),
              },
            }
          : {}),
        ...(input.versionId != null ? { versionId: input.versionId } : {}),
        ...(input.draftReport != null ? { draftReport: input.draftReport } : {}),
      },
      AbortSignal.timeout(115000),
    );
    return NextResponse.json(result);
  } catch (error) {
    // The agent's own 4xx (a version of another incident, no official version, ...) is kept for the panel to show;
    // its 5xx (model or store failure) is a bad gateway here.
    if (error instanceof UpstreamError)
      return NextResponse.json({ error: error.message }, { status: error.status >= 400 && error.status < 500 ? error.status : 502 });
    return errorResponse(error, 'Follow-up chat failed');
  }
}

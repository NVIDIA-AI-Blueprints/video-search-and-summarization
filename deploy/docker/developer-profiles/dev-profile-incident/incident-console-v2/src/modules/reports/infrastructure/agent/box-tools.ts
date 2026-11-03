// SPDX-License-Identifier: Apache-2.0

// The agent's reviewer-box endpoints: POST /api/v1/incidents/{videoId}/box/describe (the model reads a boxed region) and
// .../box/track (the tracker follows the box by name and description). Server-side only; the console maps the snake_case
// answers to its camelCase domain and never persists anything here.

import type { ServiceConfiguration } from '@/lib/env';
import { AgentUnreachableError } from '@/modules/reports/infrastructure/agent/evidence-embeddings';
import {
  type Box,
  type BoxDescribeResult,
  parseBoxProposal,
  parseReviewerTrack,
  type ReviewerTrack,
} from '@/modules/reports/domain/reviewer-item';

/** The agent answered with an error status; `status` is its own, for the route to pass on when it is a 4xx. */
export class BoxAgentError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'BoxAgentError';
    this.status = status;
  }
}

/** The agent tries the model twice (up to 150 s each, reasoning off, about 3 s when healthy); the console waits longer so a failure shows the agent's own message. */
const DESCRIBE_TIMEOUT_MS = 330_000;
/** The agent answers within about 60 s; the extra margin is for the 15 s detector timeout it may spend re-anchoring. */
const TRACK_TIMEOUT_MS = 90_000;

/** The agent's plain `{"detail": "..."}` message, when it sent one as a string. */
function agentDetail(text: string): string | null {
  try {
    const detail = (JSON.parse(text) as { detail?: unknown }).detail;
    return typeof detail === 'string' && detail ? detail : null;
  } catch {
    return null;
  }
}

async function postBox(config: ServiceConfiguration, videoId: string, action: 'describe' | 'track', body: unknown, timeoutMs: number) {
  if (!config.agentUrl) throw new Error('INCIDENT_AGENT_BASE_URL is not configured; the agent reads the reviewer box');
  const endpoint = `${config.agentUrl.replace(/\/$/, '')}/api/v1/incidents/${encodeURIComponent(videoId)}/box/${action}`;
  let response: Response;
  try {
    response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    throw new AgentUnreachableError(
      `Could not reach the agent to ${action} the box: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
  if (!response.ok) {
    const text = (await response.text().catch(() => '')).slice(0, 300);
    const detail = agentDetail(text);
    throw new BoxAgentError(detail ?? `The agent returned HTTP ${response.status} for the box ${action}: ${text}`, response.status);
  }
  return (await response.json().catch(() => null)) as Record<string, unknown> | null;
}

export async function describeBox(
  config: ServiceConfiguration,
  videoId: string,
  request: { seconds: number; box: Box },
): Promise<BoxDescribeResult> {
  const body = await postBox(config, videoId, 'describe', request, DESCRIBE_TIMEOUT_MS);
  const proposal = parseBoxProposal(body?.proposal);
  const reason = typeof body?.reason === 'string' && body.reason ? body.reason : null;
  return { proposal, reason: proposal ? reason : (reason ?? 'The model could not identify the item') };
}

export interface BoxTrackRequest {
  seconds: number;
  box: Box;
  itemType: string;
  name: string;
  description: string;
  backSeconds?: number;
  forwardSeconds?: number;
}

export async function trackBox(config: ServiceConfiguration, videoId: string, request: BoxTrackRequest): Promise<{ track: ReviewerTrack }> {
  const body = await postBox(
    config,
    videoId,
    'track',
    {
      seconds: request.seconds,
      box: request.box,
      item_type: request.itemType,
      name: request.name,
      description: request.description,
      back_seconds: request.backSeconds ?? 3,
      forward_seconds: request.forwardSeconds ?? 3,
    },
    TRACK_TIMEOUT_MS,
  );
  const track = parseReviewerTrack(body?.track);
  if (!track) throw new BoxAgentError('The agent returned no usable track', 502);
  return { track };
}

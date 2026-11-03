// SPDX-License-Identifier: Apache-2.0

// Typed browser client for the console's reviewer-box proxy routes (they call the agent server-side). The box tool uses
// these two functions only; nothing here is stored until Save.

import {
  type Box,
  type BoxDescribeResult,
  parseBoxProposal,
  parseReviewerTrack,
  type ReviewerTrack,
} from '@/modules/reports/domain/reviewer-item';

async function post(videoId: string, action: 'describe' | 'track', body: unknown, signal?: AbortSignal): Promise<Record<string, unknown>> {
  const response = await fetch(`/api/reports/${encodeURIComponent(videoId)}/box/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  const answer = (await response.json().catch(() => null)) as Record<string, unknown> | null;
  if (!response.ok) throw new Error(typeof answer?.error === 'string' ? answer.error : `The ${action} request failed (${response.status})`);
  return answer ?? {};
}

/** Asks the model what is in the box at a second. `proposal` is null with a plain `reason` when it cannot tell. */
export async function describeBoxRegion(videoId: string, seconds: number, box: Box, signal?: AbortSignal): Promise<BoxDescribeResult> {
  const answer = await post(videoId, 'describe', { seconds, box }, signal);
  const proposal = parseBoxProposal(answer.proposal);
  return { proposal, reason: typeof answer.reason === 'string' ? answer.reason : null };
}

export interface TrackBoxRequest {
  seconds: number;
  box: Box;
  itemType: string;
  name: string;
  description: string;
  backSeconds?: number;
  forwardSeconds?: number;
}

/** Tracks the box backward and forward by the reviewer's name and description. */
export async function trackBoxRegion(videoId: string, request: TrackBoxRequest, signal?: AbortSignal): Promise<ReviewerTrack> {
  const answer = await post(videoId, 'track', request, signal);
  const track = parseReviewerTrack(answer.track);
  if (!track) throw new Error('The tracker returned no usable track');
  return track;
}

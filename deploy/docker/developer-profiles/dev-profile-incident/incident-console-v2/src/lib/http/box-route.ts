// SPDX-License-Identifier: Apache-2.0

import { NextResponse } from 'next/server';

import { AgentUnreachableError } from '@/modules/reports/infrastructure/agent/evidence-embeddings';
import { BoxAgentError } from '@/modules/reports/infrastructure/agent/box-tools';
import { errorResponse } from '@/lib/http';
import type { Box } from '@/modules/reports/domain/reviewer-item';

/** The second and 0-1000 box every box request carries; null when either is missing or out of range. */
export function parseSecondsAndBox(body: Record<string, unknown>): { seconds: number; box: Box } | null {
  const { seconds, box } = body;
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return null;
  if (!Array.isArray(box) || box.length !== 4 || !box.every((n) => typeof n === 'number' && n >= 0 && n <= 1000)) return null;
  const [ymin, xmin, ymax, xmax] = box as number[];
  if (ymax <= ymin || xmax <= xmin) return null;
  return { seconds, box: box as Box };
}

/** The agent's failure as the route's answer: unreachable or 5xx is 502, a 4xx from the agent is passed on. */
export function boxAgentFailure(error: unknown, fallback: string): NextResponse {
  if (error instanceof AgentUnreachableError) return NextResponse.json({ error: error.message }, { status: 502 });
  if (error instanceof BoxAgentError) {
    const status = error.status >= 400 && error.status < 500 ? error.status : 502;
    return NextResponse.json({ error: error.message }, { status });
  }
  return errorResponse(error, fallback);
}

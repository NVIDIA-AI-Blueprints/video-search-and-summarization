// SPDX-License-Identifier: Apache-2.0

import { NextResponse } from 'next/server';

import { getServiceConfiguration } from '@/lib/env';
import { boxAgentFailure, parseSecondsAndBox } from '@/lib/http/box-route';
import { describeBox } from '@/modules/reports/infrastructure/agent/box-tools';

export const maxDuration = 60;

/** Body {seconds, box}; asks the agent to read the boxed region. Answers {proposal, reason}; nothing is stored. */
export async function POST(request: Request, context: { params: Promise<{ videoId: string }> }) {
  try {
    const { videoId } = await context.params;
    const body = (await request.json().catch(() => null)) as Record<string, unknown> | null;
    const target = body && typeof body === 'object' ? parseSecondsAndBox(body) : null;
    if (!target) return NextResponse.json({ error: 'seconds and a box [ymin, xmin, ymax, xmax] on 0-1000 are required' }, { status: 400 });
    return NextResponse.json(await describeBox(getServiceConfiguration(), videoId, target));
  } catch (error) {
    return boxAgentFailure(error, 'Could not read the box');
  }
}

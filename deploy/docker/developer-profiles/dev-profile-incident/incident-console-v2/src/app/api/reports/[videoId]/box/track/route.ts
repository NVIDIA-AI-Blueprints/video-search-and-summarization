// SPDX-License-Identifier: Apache-2.0

import { NextResponse } from 'next/server';

import { getServiceConfiguration } from '@/lib/env';
import { boxAgentFailure, parseSecondsAndBox } from '@/lib/http/box-route';
import { trackBox } from '@/modules/reports/infrastructure/agent/box-tools';

export const maxDuration = 90;

const ITEM_TYPES = ['entity', 'instrument', 'asset'];
/** The window either side of the box's second, in seconds; the contract's default is 3. */
const windowSeconds = (value: unknown): number | undefined | null =>
  value === undefined ? undefined : typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 30 ? value : null;

/** Body {seconds, box, itemType, name, description, backSeconds?, forwardSeconds?}; answers {track}; nothing is stored. */
export async function POST(request: Request, context: { params: Promise<{ videoId: string }> }) {
  try {
    const { videoId } = await context.params;
    const body = (await request.json().catch(() => null)) as Record<string, unknown> | null;
    const target = body && typeof body === 'object' ? parseSecondsAndBox(body) : null;
    const back = windowSeconds(body?.backSeconds);
    const forward = windowSeconds(body?.forwardSeconds);
    if (
      !body ||
      !target ||
      typeof body.itemType !== 'string' ||
      !ITEM_TYPES.includes(body.itemType) ||
      typeof body.name !== 'string' ||
      !body.name.trim() ||
      typeof body.description !== 'string' ||
      back === null ||
      forward === null
    ) {
      return NextResponse.json(
        { error: 'seconds, box, itemType (entity, instrument or asset), name and description are required; the windows are 0-30 s' },
        { status: 400 },
      );
    }
    return NextResponse.json(
      await trackBox(getServiceConfiguration(), videoId, {
        ...target,
        itemType: body.itemType,
        name: body.name,
        description: body.description,
        backSeconds: back,
        forwardSeconds: forward,
      }),
    );
  } catch (error) {
    return boxAgentFailure(error, 'Could not track the box');
  }
}

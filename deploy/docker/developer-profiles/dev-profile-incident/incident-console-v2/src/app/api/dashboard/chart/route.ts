// SPDX-License-Identifier: Apache-2.0

import { NextResponse } from 'next/server';
import { z } from 'zod';

import { getChartInterpreter, getChartRenderer, getDbAdapters } from '@/lib/container';
import { errorResponse } from '@/lib/http';
import { isValidTimeZone } from '@/lib/time';
import { generateChart } from '@/modules/dashboard/application/generate-chart';
import { ChartInterpreterError } from '@/modules/dashboard/domain/ChartInterpreter';
import { ChartRenderError } from '@/modules/dashboard/domain/ChartRenderer';
import { CHART_REQUEST_MAX_LENGTH, CHART_TYPES } from '@/modules/dashboard/domain/generated-chart';

// One request, one answer: nothing else is accepted, so no history can reach the model.
const body = z
  .object({
    request: z.string().trim().min(1).max(CHART_REQUEST_MAX_LENGTH),
    chartType: z.enum(CHART_TYPES),
    timezone: z.string().optional(),
  })
  .strict();

export async function POST(request: Request) {
  try {
    const input = body.safeParse(await request.json().catch(() => null));
    if (!input.success) {
      return NextResponse.json(
        { error: `Pick bar or pie and describe the chart in 1 to ${CHART_REQUEST_MAX_LENGTH} characters` },
        { status: 400 },
      );
    }
    const timezone = input.data.timezone ?? 'UTC';
    if (!isValidTimeZone(timezone)) return NextResponse.json({ error: 'Invalid time zone' }, { status: 400 });
    const interpreter = getChartInterpreter();
    // Both ports are the agent: without its URL no chart can be read or drawn.
    if (!interpreter)
      return NextResponse.json({ error: 'Chart requests are not set up: INCIDENT_AGENT_BASE_URL is missing' }, { status: 503 });
    const renderer = getChartRenderer();
    if (!renderer) return NextResponse.json({ error: 'Chart drawing is not set up: INCIDENT_AGENT_BASE_URL is missing' }, { status: 503 });
    const { dashboard } = getDbAdapters();
    const { request: text, chartType } = input.data;
    return NextResponse.json(
      await generateChart({ interpreter, dashboard, renderer }, { request: text, chartType, timezone, now: new Date() }),
    );
  } catch (error) {
    if (error instanceof ChartInterpreterError) {
      // The agent answers 501 when this profile has no chart LLM: a setup problem, not a model failure to retry.
      if (error.status === 501) return NextResponse.json({ error: 'Chart requests are not set up on this profile.' }, { status: 503 });
      return NextResponse.json({ error: 'The model that reads chart requests did not answer. Try again.' }, { status: 502 });
    }
    if (error instanceof ChartRenderError) {
      return NextResponse.json({ error: 'The chart service could not draw this chart. Try again.' }, { status: 502 });
    }
    return errorResponse(error, 'Could not build the chart');
  }
}

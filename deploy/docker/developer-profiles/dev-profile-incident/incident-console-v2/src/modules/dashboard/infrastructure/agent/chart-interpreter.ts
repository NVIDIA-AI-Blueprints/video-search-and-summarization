// SPDX-License-Identifier: Apache-2.0

// Reads a chart request through the VSS agent (POST /api/v1/incidents/chart-requests), which calls one of
// the agent's own configured LLMs with this prompt and the one request. Its reply is the JSON object the
// model wrote, or null, and is returned unvalidated: parseChartSpec in generateChart decides whether it counts.

import { ChartInterpreterError, type ChartInterpreter } from '@/modules/dashboard/domain/ChartInterpreter';
import { CHART_SYSTEM_PROMPT } from '@/modules/dashboard/domain/chart-prompt';

const TIMEOUT_MS = 45_000;

export class AgentChartInterpreter implements ChartInterpreter {
  private readonly baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
  }

  async interpret(request: string): Promise<unknown> {
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}/api/v1/incidents/chart-requests`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: [
            { role: 'system', content: CHART_SYSTEM_PROMPT },
            { role: 'user', content: request },
          ],
        }),
        cache: 'no-store',
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });
    } catch (error) {
      throw new ChartInterpreterError(`The agent could not be reached: ${error instanceof Error ? error.message : String(error)}`);
    }
    if (!response.ok) throw new ChartInterpreterError(`The agent returned HTTP ${response.status} for a chart request`, response.status);
    const payload = (await response.json().catch(() => null)) as { reply?: unknown } | null;
    if (!payload || !('reply' in payload)) throw new ChartInterpreterError('The agent sent no reply field for a chart request');
    return payload.reply ?? null;
  }
}

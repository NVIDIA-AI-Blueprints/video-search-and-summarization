// SPDX-License-Identifier: Apache-2.0

// ChartInterpreter: turns one chart request into a model's raw reply. It sees
// only the request text, never any data, and each call stands alone. The reply
// is untrusted until parseChartSpec accepts it.

export interface ChartInterpreter {
  /** The model's reply as the JSON object it wrote; null when it wrote none. Throws ChartInterpreterError when no model answered. */
  interpret(request: string): Promise<unknown>;
}

/**
 * No model answered: the call failed or its reply was malformed. The text is for logs only, never shown.
 * `status` is the agent's HTTP status when it answered (501 means the profile has no chart LLM configured).
 */
export class ChartInterpreterError extends Error {
  readonly status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = 'ChartInterpreterError';
    this.status = status;
  }
}

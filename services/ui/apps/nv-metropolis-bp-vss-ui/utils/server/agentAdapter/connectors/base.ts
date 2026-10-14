// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { ConnectorEvent, CreateRunRequest, JsonObject } from "../contract";

export class ConnectorError extends Error {
  /**
   * False only when the connector knows the request never reached the backend,
   * so running the turn again cannot repeat work. Undefined means unknown.
   */
  readonly delivered?: false;

  constructor(
    message: string,
    readonly code = "backend_error",
    readonly retryable = false,
    options?: ErrorOptions & { delivered?: false }
  ) {
    super(message, options);
    if (options?.delivered === false) this.delivered = false;
  }
}

export interface Connector {
  readonly protocol: string;
  readonly capabilities: JsonObject;
  run(
    request: CreateRunRequest,
    runId: string,
    signal: AbortSignal
  ): AsyncGenerator<ConnectorEvent>;
  cancel(runId: string): void | Promise<void>;
}

export const connectorCapabilities = (
  protocol: string,
  overrides: JsonObject = {}
): JsonObject => ({
  protocol,
  streaming: true,
  tool_events: "best_effort",
  artifacts: true,
  interactions: false,
  ...overrides,
});

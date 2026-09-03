// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { ConnectorEvent, CreateRunRequest, JsonObject } from "../contract";

export class ConnectorError extends Error {
  constructor(
    message: string,
    readonly code = "backend_error",
    readonly retryable = false
  ) {
    super(message);
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

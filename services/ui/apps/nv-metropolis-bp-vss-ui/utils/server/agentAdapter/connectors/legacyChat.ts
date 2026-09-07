// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { AgentAdapterConfig } from "../config";
import {
  type ConnectorEvent,
  type CreateRunRequest,
  fullTranscript,
} from "../contract";
import { isJsonObject, strictJsonParse } from "../json";
import {
  boundedResponseText,
  linkedTimeoutSignal,
  readLines,
} from "../streams";
import { type Connector, ConnectorError, connectorCapabilities } from "./base";
import { createHash } from "node:crypto";

const asString = (value: unknown, fallback: string): string =>
  typeof value === "string" ? value : fallback;

export class LegacyChatConnector implements Connector {
  readonly protocol = "legacy-chat";
  readonly capabilities = connectorCapabilities(this.protocol);
  private readonly endpoint: string;

  constructor(private readonly config: AgentAdapterConfig) {
    this.endpoint = `${config.backendUrl}${config.backendPath}`;
  }

  private static sessionKey(threadId: string): string {
    return `vss-ui-${createHash("sha256")
      .update(threadId)
      .digest("hex")
      .slice(0, 40)}`;
  }

  private static stepEvent(payload: unknown): ConnectorEvent | null {
    if (!isJsonObject(payload)) return null;
    const status = asString(payload.status, "in_progress").toLowerCase();
    const data = {
      tool_call_id: asString(payload.id, "tool"),
      name: asString(payload.name, "Agent step"),
      payload: payload.payload,
    };
    if (["complete", "completed", "success", "succeeded"].includes(status)) {
      return { type: "tool.completed", data };
    }
    if (["error", "failed", "failure"].includes(status)) {
      return {
        type: "tool.failed",
        data: { ...data, error: payload.error },
      };
    }
    return { type: "tool.started", data };
  }

  private static content(payload: unknown): string | null {
    if (!isJsonObject(payload) || !Array.isArray(payload.choices)) return null;
    const choice = payload.choices[0];
    if (!isJsonObject(choice)) return null;
    if (
      isJsonObject(choice.delta) &&
      typeof choice.delta.content === "string"
    ) {
      return choice.delta.content;
    }
    if (
      isJsonObject(choice.message) &&
      typeof choice.message.content === "string"
    ) {
      return choice.message.content;
    }
    return null;
  }

  async *run(
    request: CreateRunRequest,
    _runId: string,
    signal: AbortSignal
  ): AsyncGenerator<ConnectorEvent> {
    const timeout = linkedTimeoutSignal(signal, this.config.requestTimeoutMs);
    const headers = new Headers({
      Accept: "text/event-stream",
      "Content-Type": "application/json",
      "Conversation-Id": LegacyChatConnector.sessionKey(request.threadId),
      "User-Agent": "vss-next-agent-adapter/1.0",
      ...this.config.backendHeaders,
    });
    if (this.config.backendToken) {
      headers.set("Authorization", `Bearer ${this.config.backendToken}`);
    }
    let response: Response;
    try {
      response = await fetch(this.endpoint, {
        method: "POST",
        headers,
        body: JSON.stringify({
          model: this.config.backendModel,
          messages: fullTranscript(request),
          stream: true,
        }),
        signal: timeout.signal,
      });
    } catch (error) {
      timeout.cleanup();
      if (signal.aborted) return;
      if (timeout.signal.aborted) {
        throw new ConnectorError("backend timed out", "backend_timeout", true, {
          cause: error,
        });
      }
      throw new ConnectorError(
        "backend is unreachable",
        "backend_unreachable",
        true,
        { cause: error }
      );
    }
    if (!response.ok) {
      await boundedResponseText(response, 64_000).catch(() => "");
      timeout.cleanup();
      throw new ConnectorError(
        `backend returned HTTP ${response.status}`,
        "backend_http_error",
        response.status >= 500
      );
    }

    let done = false;
    try {
      for await (const rawLine of readLines(response)) {
        if (signal.aborted) return;
        const line = rawLine.trim();
        if (!line) continue;
        if (line.startsWith("intermediate_data:")) {
          try {
            const event = LegacyChatConnector.stepEvent(
              strictJsonParse(line.slice(line.indexOf(":") + 1).trim())
            );
            if (event) yield event;
          } catch {
            // The legacy stream historically ignored malformed intermediate rows.
          }
          continue;
        }
        if (!line.startsWith("data:")) continue;
        const data = line.slice(line.indexOf(":") + 1).trim();
        if (data === "[DONE]") {
          done = true;
          break;
        }
        try {
          const content = LegacyChatConnector.content(strictJsonParse(data));
          if (content)
            yield { type: "message.delta", data: { delta: content } };
        } catch {
          // Preserve compatibility with the original best-effort legacy parser.
        }
      }
    } catch (error) {
      if (signal.aborted) return;
      throw new ConnectorError(
        timeout.signal.aborted
          ? "backend timed out"
          : "backend stream ended unexpectedly",
        timeout.signal.aborted ? "backend_timeout" : "backend_stream_error",
        true,
        { cause: error }
      );
    } finally {
      timeout.cleanup();
    }
    if (!done && !signal.aborted) {
      throw new ConnectorError(
        "backend stream ended before [DONE]",
        "incomplete_backend_stream",
        true
      );
    }
  }

  cancel(): void {
    // The shared AbortController passed to fetch performs cancellation.
  }
}

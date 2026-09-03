// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  ARTIFACT_CLOSE,
  ARTIFACT_OPEN,
  parseArtifact,
  stripArtifactEnvelopes,
} from "../artifacts";
import type { EmbeddedGatewayConfig } from "../config";
import {
  type ConnectorEvent,
  type CreateRunRequest,
  type JsonObject,
  type Message,
  historyPrefix,
  fullTranscript,
  stableStringify,
  toResponsesItem,
  transcriptDigest,
} from "../contract";
import { isJsonObject, strictJsonParse } from "../json";
import { boundedResponseText, linkedTimeoutSignal, readSse } from "../streams";
import { type Connector, ConnectorError, connectorCapabilities } from "./base";
import { createHash } from "node:crypto";

const PUBLISH_ARTIFACT_TOOL = "vss_ui_publish_artifact";
const MAX_CLIENT_TOOL_ROUNDS = 4;
const SUPPORTED_ARTIFACT_KINDS = new Set([
  "vss.search.results",
  "vss.alert.incidents",
]);
const ARTIFACT_TRANSPORT_INSTRUCTIONS = `VSS UI artifact transport contract:
- If this turn successfully produces a validated VSS search result or alert-incident result and the vss_ui_publish_artifact tool is available, you must call that tool exactly once with the exact version, kind, and payload required by the loaded VSS skill. Human-readable prose or a table is not a substitute.
- Do not call the publisher for ordinary chat, failed operations, unvalidated data, alert-rule inventory, or alert-rule mutation.
- When the publisher is unavailable, follow the loaded VSS skill's vss-ui-artifact envelope fallback.
- After a successful publisher result, finish the human-facing response without repeating its machine-readable payload.`;
const PUBLISH_ARTIFACT_DEFINITION: JsonObject = {
  type: "function",
  name: PUBLISH_ARTIFACT_TOOL,
  description:
    "Publish a validated VSS search-result or alert-incident payload to the VSS UI. Use this exactly once when an installed VSS skill requires a UI artifact; do not use it for ordinary chat or unvalidated data.",
  parameters: {
    type: "object",
    properties: {
      version: { type: "string", enum: ["1.0"] },
      kind: {
        type: "string",
        enum: [...SUPPORTED_ARTIFACT_KINDS].sort(),
      },
      payload: { type: "object" },
    },
    required: ["version", "kind", "payload"],
    additionalProperties: false,
  },
};

interface ThreadState {
  previousResponseId: string;
  transcript: Message[];
  transcriptDigest: string;
  transcriptChars: number;
}

interface SelectedInput {
  selected: Message[];
  previousResponseId?: string;
  transcript: Message[];
}

const asString = (value: unknown): string | undefined =>
  typeof value === "string" ? value : undefined;

export class ResponsesConnector implements Connector {
  readonly protocol = "responses";
  readonly capabilities = connectorCapabilities(this.protocol);
  private readonly endpoint: string;
  private readonly threadState = new Map<string, ThreadState>();
  private threadStateChars = 0;

  constructor(private readonly config: EmbeddedGatewayConfig) {
    this.endpoint = `${config.backendUrl}${config.backendPath}`;
  }

  private sessionKey(threadId: string): string {
    return `vss-ui:${createHash("sha256")
      .update(`vss-ui:${threadId}`)
      .digest("hex")
      .slice(0, 40)}`;
  }

  private selectInput(request: CreateRunRequest): SelectedInput {
    const state = this.threadState.get(request.threadId);
    if (state) {
      this.threadState.delete(request.threadId);
      this.threadState.set(request.threadId, state);
    }
    const prefix = historyPrefix(request);
    if (
      state?.previousResponseId &&
      (!request.history.length ||
        transcriptDigest(prefix) === state.transcriptDigest)
    ) {
      return {
        selected: request.input,
        previousResponseId: state.previousResponseId,
        transcript: [...state.transcript, ...request.input],
      };
    }
    const selected = fullTranscript(request);
    return { selected, transcript: selected };
  }

  private get artifactPublisherEnabled(): boolean {
    return !!this.config.vssCapabilities;
  }

  private instructions(requested?: string): string | undefined {
    if (!this.artifactPublisherEnabled) return requested;
    return requested
      ? `${requested}\n\n${ARTIFACT_TRANSPORT_INSTRUCTIONS}`
      : ARTIFACT_TRANSPORT_INSTRUCTIONS;
  }

  private requestPayload(request: CreateRunRequest): {
    payload: JsonObject;
    transcript: Message[];
  } {
    const selected = this.selectInput(request);
    const payload: JsonObject = {
      model: this.config.backendModel,
      input: selected.selected.map(toResponsesItem),
      stream: true,
      store: true,
    };
    if (this.artifactPublisherEnabled) {
      payload.tools = [PUBLISH_ARTIFACT_DEFINITION];
    }
    if (selected.previousResponseId) {
      payload.previous_response_id = selected.previousResponseId;
    }
    const instructions = this.instructions(request.instructions);
    if (instructions) payload.instructions = instructions;
    if (this.config.backendSessionField) {
      payload[this.config.backendSessionField] = this.sessionKey(
        request.threadId
      );
    }
    return { payload, transcript: selected.transcript };
  }

  private clientToolPayload(
    request: CreateRunRequest,
    previousResponseId: string,
    outputs: JsonObject[]
  ): JsonObject {
    const payload: JsonObject = {
      model: this.config.backendModel,
      input: outputs,
      previous_response_id: previousResponseId,
      stream: true,
      store: true,
    };
    if (this.artifactPublisherEnabled) {
      payload.tools = [PUBLISH_ARTIFACT_DEFINITION];
    }
    const instructions = this.instructions(request.instructions);
    if (instructions) payload.instructions = instructions;
    if (this.config.backendSessionField) {
      payload[this.config.backendSessionField] = this.sessionKey(
        request.threadId
      );
    }
    return payload;
  }

  private headers(threadId: string): Headers {
    const headers = new Headers({
      Accept: "text/event-stream",
      "Content-Type": "application/json",
      "User-Agent": "vss-next-agent-adapter/1.0",
      ...this.config.backendHeaders,
    });
    if (this.config.backendToken) {
      headers.set("Authorization", `Bearer ${this.config.backendToken}`);
    }
    if (this.config.backendSessionHeader) {
      headers.set(this.config.backendSessionHeader, this.sessionKey(threadId));
    }
    return headers;
  }

  private static errorMessage(payload: unknown, fallback: string): string {
    if (isJsonObject(payload)) {
      if (
        isJsonObject(payload.error) &&
        typeof payload.error.message === "string"
      ) {
        return payload.error.message;
      }
      if (
        isJsonObject(payload.response) &&
        isJsonObject(payload.response.error) &&
        typeof payload.response.error.message === "string"
      ) {
        return payload.response.error.message;
      }
    }
    return fallback;
  }

  private static responseId(payload: unknown): string | undefined {
    if (!isJsonObject(payload)) return undefined;
    if (
      isJsonObject(payload.response) &&
      typeof payload.response.id === "string"
    ) {
      return payload.response.id;
    }
    return typeof payload.id === "string" && payload.id.startsWith("resp_")
      ? payload.id
      : undefined;
  }

  private static functionCall(payload: unknown): JsonObject | undefined {
    return isJsonObject(payload) &&
      isJsonObject(payload.item) &&
      payload.item.type === "function_call"
      ? payload.item
      : undefined;
  }

  private static functionCallOutput(payload: unknown): JsonObject | undefined {
    return isJsonObject(payload) &&
      isJsonObject(payload.item) &&
      payload.item.type === "function_call_output"
      ? payload.item
      : undefined;
  }

  private static publishArtifactResult(
    callId: string,
    argumentsText: string
  ): { event: ConnectorEvent; output: JsonObject } {
    const artifact = parseArtifact(argumentsText);
    if (!artifact || !SUPPORTED_ARTIFACT_KINDS.has(artifact.kind)) {
      const message =
        "artifact arguments must be a valid version 1.0 VSS search or alert artifact";
      return {
        event: {
          type: "tool.failed",
          data: {
            tool_call_id: callId,
            name: PUBLISH_ARTIFACT_TOOL,
            arguments: argumentsText,
            error: message,
          },
        },
        output: {
          type: "function_call_output",
          call_id: callId,
          output: JSON.stringify({ ok: false, error: message }),
        },
      };
    }
    const envelope = stableStringify({
      version: "1.0",
      kind: artifact.kind,
      payload: artifact.payload,
    });
    return {
      event: {
        type: "tool.completed",
        data: {
          tool_call_id: callId,
          name: PUBLISH_ARTIFACT_TOOL,
          arguments: argumentsText,
          output: `${ARTIFACT_OPEN}${envelope}${ARTIFACT_CLOSE}`,
        },
      },
      output: {
        type: "function_call_output",
        call_id: callId,
        output: JSON.stringify({
          ok: true,
          artifact_id: artifact.artifactId,
          message: "Published to the VSS UI; finish the human-facing answer.",
        }),
      },
    };
  }

  private recordState(
    request: CreateRunRequest,
    responseId: string,
    transcript: Message[],
    outputText: string
  ): void {
    const completedTranscript: Message[] = [
      ...transcript,
      { role: "assistant", content: stripArtifactEnvelopes(outputText) },
    ];
    const state: ThreadState = {
      previousResponseId: responseId,
      transcript: completedTranscript,
      transcriptDigest: transcriptDigest(completedTranscript),
      transcriptChars: completedTranscript.reduce(
        (total, message) => total + message.content.length,
        0
      ),
    };
    const previous = this.threadState.get(request.threadId);
    if (previous) {
      this.threadState.delete(request.threadId);
      this.threadStateChars -= previous.transcriptChars;
    }
    if (state.transcriptChars > this.config.maxThreadStateChars) return;
    this.threadState.set(request.threadId, state);
    this.threadStateChars += state.transcriptChars;
    while (
      this.threadState.size > this.config.maxRuns ||
      this.threadStateChars > this.config.maxThreadStateChars
    ) {
      const oldest = this.threadState.entries().next().value as
        | [string, ThreadState]
        | undefined;
      if (!oldest) break;
      this.threadState.delete(oldest[0]);
      this.threadStateChars -= oldest[1].transcriptChars;
    }
  }

  private async request(
    payload: JsonObject,
    threadId: string,
    signal: AbortSignal
  ): Promise<{ response: Response; cleanup: () => void }> {
    const timeout = linkedTimeoutSignal(signal, this.config.requestTimeoutMs);
    try {
      const response = await fetch(this.endpoint, {
        method: "POST",
        headers: this.headers(threadId),
        body: JSON.stringify(payload),
        signal: timeout.signal,
      });
      if (!response.ok) {
        let parsed: unknown = {};
        try {
          parsed = strictJsonParse(await boundedResponseText(response, 64_000));
        } catch {
          // Use a status-only fallback for malformed upstream errors.
        }
        timeout.cleanup();
        throw new ConnectorError(
          ResponsesConnector.errorMessage(
            parsed,
            `backend returned HTTP ${response.status}`
          ),
          "backend_http_error",
          response.status >= 500
        );
      }
      return { response, cleanup: timeout.cleanup };
    } catch (error) {
      if (error instanceof ConnectorError) throw error;
      timeout.cleanup();
      if (signal.aborted) throw error;
      if (timeout.signal.aborted) {
        throw new ConnectorError("backend timed out", "backend_timeout", true);
      }
      throw new ConnectorError(
        "backend is unreachable",
        "backend_unreachable",
        true
      );
    }
  }

  async *run(
    request: CreateRunRequest,
    _runId: string,
    signal: AbortSignal
  ): AsyncGenerator<ConnectorEvent> {
    const initial = this.requestPayload(request);
    let payload = initial.payload;
    const transcript = initial.transcript;
    let responseId: string | undefined;
    const outputParts: string[] = [];
    let retainedOutputChars = transcript.reduce(
      (total, message) => total + message.content.length,
      0
    );
    let retainThreadState =
      retainedOutputChars <= this.config.maxThreadStateChars;
    const toolNames = new Map<string, string>();
    const toolArguments = new Map<string, string>();
    const canonicalToolIds = new Map<string, string>();
    let clientToolRounds = 0;

    const retainOutput = (delta: string): void => {
      if (!retainThreadState) return;
      retainedOutputChars += delta.length;
      if (retainedOutputChars > this.config.maxThreadStateChars) {
        outputParts.length = 0;
        retainThreadState = false;
        return;
      }
      outputParts.push(delta);
    };

    while (true) {
      let completed = false;
      let roundResponseId: string | undefined;
      const clientToolOutputs: JsonObject[] = [];
      let response: Response;
      let cleanup: () => void;
      try {
        ({ response, cleanup } = await this.request(
          payload,
          request.threadId,
          signal
        ));
      } catch (error) {
        if (signal.aborted) return;
        throw error;
      }

      try {
        const contentType = response.headers.get("content-type") ?? "";
        if (!contentType.toLowerCase().includes("text/event-stream")) {
          let parsed: unknown;
          try {
            parsed = strictJsonParse(
              await boundedResponseText(response, 5_000_000)
            );
          } catch {
            throw new ConnectorError(
              "backend returned a non-SSE response",
              "invalid_backend_response"
            );
          }
          roundResponseId = ResponsesConnector.responseId(parsed);
          if (isJsonObject(parsed) && typeof parsed.output_text === "string") {
            retainOutput(parsed.output_text);
            yield {
              type: "message.delta",
              data: { delta: parsed.output_text },
            };
          }
          completed = true;
        } else {
          for await (const frame of readSse(response)) {
            if (signal.aborted) return;
            if (frame.data.trim() === "[DONE]") break;
            let eventPayload: unknown;
            try {
              eventPayload = strictJsonParse(frame.data);
            } catch {
              throw new ConnectorError(
                "backend emitted invalid SSE JSON",
                "invalid_backend_event"
              );
            }
            const eventType =
              frame.event ||
              (isJsonObject(eventPayload)
                ? asString(eventPayload.type)
                : undefined) ||
              "";
            roundResponseId =
              ResponsesConnector.responseId(eventPayload) ?? roundResponseId;

            if (eventType === "response.output_text.delta") {
              const delta = isJsonObject(eventPayload)
                ? asString(eventPayload.delta)
                : undefined;
              if (delta) {
                retainOutput(delta);
                yield { type: "message.delta", data: { delta } };
              }
            } else if (eventType === "response.output_item.added") {
              const item = ResponsesConnector.functionCall(eventPayload);
              if (item) {
                const itemId = String(item.id || item.call_id || "");
                const callId = String(item.call_id || itemId);
                const name = String(item.name || "tool");
                for (const identifier of new Set([itemId, callId])) {
                  if (!identifier) continue;
                  toolNames.set(identifier, name);
                  canonicalToolIds.set(identifier, callId);
                }
                toolArguments.set(callId, toolArguments.get(callId) ?? "");
                yield {
                  type: "tool.started",
                  data: { tool_call_id: callId, name },
                };
              }
            } else if (
              eventType === "response.function_call_arguments.delta" &&
              isJsonObject(eventPayload)
            ) {
              const identifier = String(
                eventPayload.item_id || eventPayload.call_id || ""
              );
              const delta = asString(eventPayload.delta);
              if (identifier && delta !== undefined) {
                const callId = canonicalToolIds.get(identifier) ?? identifier;
                toolArguments.set(
                  callId,
                  (toolArguments.get(callId) ?? "") + delta
                );
                yield {
                  type: "tool.arguments.delta",
                  data: {
                    tool_call_id: callId,
                    name:
                      toolNames.get(identifier) ??
                      toolNames.get(callId) ??
                      "tool",
                    delta,
                  },
                };
              }
            } else if (eventType === "response.output_item.done") {
              const item = ResponsesConnector.functionCall(eventPayload);
              if (item) {
                const itemId = String(item.id || item.call_id || "");
                const callId = String(item.call_id || itemId);
                const argumentsText =
                  typeof item.arguments === "string"
                    ? item.arguments
                    : toolArguments.get(callId) ?? "";
                const name = String(
                  item.name || toolNames.get(itemId) || "tool"
                );
                if (name === PUBLISH_ARTIFACT_TOOL) {
                  yield {
                    type: "tool.requested",
                    data: {
                      tool_call_id: callId,
                      name,
                      arguments: argumentsText,
                    },
                  };
                  const published = ResponsesConnector.publishArtifactResult(
                    callId,
                    argumentsText
                  );
                  yield published.event;
                  clientToolOutputs.push(published.output);
                } else {
                  yield {
                    type:
                      item.status === "completed"
                        ? "tool.completed"
                        : "tool.requested",
                    data: {
                      tool_call_id: callId,
                      name,
                      arguments: argumentsText,
                    },
                  };
                }
              }
              const output =
                ResponsesConnector.functionCallOutput(eventPayload);
              if (output) {
                const callId = String(output.call_id || output.id || "tool");
                yield {
                  type: "tool.completed",
                  data: {
                    tool_call_id: callId,
                    name: toolNames.get(callId) ?? "tool",
                    output: output.output,
                  },
                };
              }
            } else if (eventType === "response.completed") {
              completed = true;
            } else if (
              eventType === "response.failed" ||
              eventType === "error"
            ) {
              throw new ConnectorError(
                ResponsesConnector.errorMessage(
                  eventPayload,
                  "backend run failed"
                ),
                "backend_run_failed"
              );
            }
          }
        }
      } catch (error) {
        if (signal.aborted) return;
        if (error instanceof ConnectorError) throw error;
        throw new ConnectorError(
          "backend stream ended unexpectedly",
          "backend_stream_error",
          true
        );
      } finally {
        cleanup();
      }

      if (signal.aborted) return;
      if (!completed) {
        throw new ConnectorError(
          "backend stream ended before response.completed",
          "incomplete_backend_stream",
          true
        );
      }
      responseId = roundResponseId ?? responseId;
      if (!clientToolOutputs.length) break;
      if (!roundResponseId) {
        throw new ConnectorError(
          "backend requested a client tool without a response id",
          "invalid_backend_response"
        );
      }
      clientToolRounds += 1;
      if (clientToolRounds > MAX_CLIENT_TOOL_ROUNDS) {
        throw new ConnectorError(
          "backend exceeded the VSS UI publisher tool round limit",
          "client_tool_round_limit"
        );
      }
      payload = this.clientToolPayload(
        request,
        roundResponseId,
        clientToolOutputs
      );
    }

    if (responseId && retainThreadState) {
      this.recordState(request, responseId, transcript, outputParts.join(""));
    }
  }

  cancel(): void {
    // The shared AbortController passed to fetch performs cancellation.
  }
}

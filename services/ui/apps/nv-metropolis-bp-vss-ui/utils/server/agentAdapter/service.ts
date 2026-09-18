// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  ARTIFACT_PROTOCOL_VERSION,
  ArtifactStreamParser,
  stripArtifactsFromValue,
} from "./artifacts";
import type { AgentAdapterConfig } from "./config";
import { type Connector, ConnectorError } from "./connectors/base";
import { LegacyChatConnector } from "./connectors/legacyChat";
import { OpenClawConnector } from "./connectors/openClaw";
import { ResponsesConnector } from "./connectors/responses";
import {
  PROTOCOL_VERSION,
  type CreateRunRequest,
  type JsonObject,
} from "./contract";
import { RunRecord, RunStore } from "./store";

const buildConnector = (config: AgentAdapterConfig): Connector => {
  if (config.backendProtocol === "openclaw-ws") {
    return new OpenClawConnector(config);
  }
  if (config.backendProtocol === "legacy-chat") {
    return new LegacyChatConnector(config);
  }
  return new ResponsesConnector(config);
};

export class AgentAdapterService {
  readonly store: RunStore;
  private readonly connector: Connector;

  constructor(readonly config: AgentAdapterConfig) {
    this.connector = buildConnector(config);
    // Reserve the thread-state ceiling up front so the independently managed
    // connector cache and run store cannot exceed the process-wide limit.
    this.store = new RunStore(
      config.runRetentionMs,
      config.maxRuns,
      config.maxEventsPerRun,
      config.maxEventCharsPerRun,
      config.maxRetainedChars - config.maxThreadStateChars
    );
  }

  capabilities(): JsonObject {
    return {
      protocol_version: PROTOCOL_VERSION,
      transport: "sse",
      features: {
        reconnect: true,
        cancellation: true,
        idempotent_run_creation: true,
        interaction_responses: false,
        artifacts: true,
      },
      artifact_protocol: {
        version: ARTIFACT_PROTOCOL_VERSION,
        transport: "connector-normalized",
        transports: [
          "openclaw-tool-result",
          "openclaw-managed-image",
          "vss-cli-completion",
          "responses-client-tool",
          "agent-tool-output",
          "agent-text-envelope",
        ],
        kinds: [
          "vss.search.results",
          "vss.alert.incidents",
          "vss.media.image",
        ],
      },
      connector: this.connector.capabilities,
      event_types: [
        "run.started",
        "message.delta",
        "reasoning.delta",
        "tool.started",
        "tool.arguments.delta",
        "tool.requested",
        "tool.completed",
        "tool.failed",
        "artifact.created",
        "interaction.required",
        "run.completed",
        "run.failed",
        "run.cancelled",
      ],
      limits: {
        max_events_per_run: this.config.maxEventsPerRun,
        max_event_chars_per_run: this.config.maxEventCharsPerRun,
        max_retained_chars: this.config.maxRetainedChars,
        run_retention_seconds: this.config.runRetentionMs / 1_000,
      },
    };
  }

  createRun(
    request: CreateRunRequest,
    idempotencyKey?: string
  ): { record: RunRecord; replayed: boolean } {
    const created = this.store.create(request, idempotencyKey);
    if (created.replayed) return created;
    created.record.append("run.started", {
      surface: request.surface,
      connector_protocol: this.connector.protocol,
    });
    void this.executeRun(created.record);
    return created;
  }

  private static appendParsedEvents(
    record: RunRecord,
    events: Array<{ type: string; data: JsonObject }>
  ): void {
    for (const event of events) record.append(event.type, event.data);
  }

  private static toolArtifactSource(data: JsonObject): unknown {
    if (data._artifact_source !== undefined) return data._artifact_source;
    if (data.output !== undefined) return data.output;
    return data.payload;
  }

  private static appendToolCompletion(
    record: RunRecord,
    parser: ArtifactStreamParser,
    rawData: JsonObject
  ): void {
    const data = { ...rawData };
    const artifacts = parser.inspectComplete(
      AgentAdapterService.toolArtifactSource(data)
    );
    delete data._artifact_source;
    if (data.output !== undefined) {
      data.output = stripArtifactsFromValue(data.output);
    } else if (data.payload !== undefined) {
      data.payload = stripArtifactsFromValue(data.payload);
    }
    record.append("tool.completed", data);
    AgentAdapterService.appendParsedEvents(record, artifacts);
  }

  private static appendConnectorEvent(
    record: RunRecord,
    parser: ArtifactStreamParser,
    type: string,
    rawData: JsonObject
  ): void {
    if (type === "message.delta" && typeof rawData.delta === "string") {
      AgentAdapterService.appendParsedEvents(
        record,
        parser.feed(rawData.delta)
      );
      return;
    }
    if (type === "artifact.source") {
      AgentAdapterService.appendParsedEvents(
        record,
        parser.inspectComplete(rawData.source)
      );
      return;
    }
    if (type === "tool.completed") {
      AgentAdapterService.appendToolCompletion(record, parser, rawData);
      return;
    }
    record.append(type, rawData);
  }

  private async executeRun(record: RunRecord): Promise<void> {
    const parser = new ArtifactStreamParser(true);
    const flush = (): void => {
      AgentAdapterService.appendParsedEvents(record, parser.finish());
    };

    try {
      for await (const event of this.connector.run(
        record.request,
        record.runId,
        record.abortController.signal
      )) {
        if (record.abortController.signal.aborted) break;
        if (event.type.startsWith("run.")) {
          throw new ConnectorError(
            "connector emitted a reserved terminal event",
            "connector_contract_error"
          );
        }
        AgentAdapterService.appendConnectorEvent(
          record,
          parser,
          event.type,
          event.data
        );
      }
      flush();
      if (record.abortController.signal.aborted) {
        this.store.finish(record, "run.cancelled", {
          reason: "client_cancelled",
        });
      } else {
        this.store.finish(record, "run.completed");
      }
    } catch (error) {
      flush();
      if (record.abortController.signal.aborted) {
        this.store.finish(record, "run.cancelled", {
          reason: "client_cancelled",
        });
      } else if (error instanceof ConnectorError) {
        this.store.finish(record, "run.failed", {
          error: {
            code: error.code,
            message: error.message,
            retryable: error.retryable,
          },
        });
      } else {
        console.error("Unexpected embedded agent adapter failure");
        this.store.finish(record, "run.failed", {
          error: {
            code: "adapter_internal_error",
            message: "the adapter could not complete this run",
            retryable: false,
          },
        });
      }
    }
  }

  async cancelRun(runId: string): Promise<RunRecord> {
    const record = this.store.get(runId);
    if (!record.terminal) {
      record.abortController.abort(new Error("client cancelled"));
      try {
        await this.connector.cancel(runId);
      } catch {
        console.error("Embedded agent connector cancellation failed");
      }
    }
    return record;
  }
}

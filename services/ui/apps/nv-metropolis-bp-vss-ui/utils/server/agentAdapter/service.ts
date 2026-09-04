// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  ARTIFACT_PROTOCOL_VERSION,
  ArtifactStreamParser,
  stripArtifactsFromValue,
} from "./artifacts";
import { capabilitySummary } from "./capabilities";
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
    this.store = new RunStore(
      config.runRetentionMs,
      config.maxRuns,
      config.maxEventsPerRun,
      config.maxEventCharsPerRun
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
          "vss-cli-completion",
          "responses-client-tool",
          "agent-tool-output",
          "agent-text-envelope",
        ],
        kinds: ["vss.search.results", "vss.alert.incidents"],
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
        run_retention_seconds: this.config.runRetentionMs / 1_000,
      },
      vss: this.config.vssCapabilities
        ? capabilitySummary(this.config.vssCapabilities)
        : { attached: false, ready: false },
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

  private async executeRun(record: RunRecord): Promise<void> {
    const parser = new ArtifactStreamParser(true);
    const append = (type: string, rawData: JsonObject): void => {
      if (type === "message.delta" && typeof rawData.delta === "string") {
        for (const parsed of parser.feed(rawData.delta)) {
          record.append(parsed.type, parsed.data);
        }
        return;
      }
      if (type === "tool.completed") {
        const data = { ...rawData };
        const artifactSource = data._artifact_source;
        delete data._artifact_source;
        const output = data.output;
        const artifacts = parser.inspectComplete(
          artifactSource === undefined ? output : artifactSource
        );
        if (output !== undefined) {
          data.output = stripArtifactsFromValue(output);
        }
        record.append(type, data);
        for (const artifact of artifacts) {
          record.append(artifact.type, artifact.data);
        }
        return;
      }
      record.append(type, rawData);
    };
    const flush = (): void => {
      for (const event of parser.finish())
        record.append(event.type, event.data);
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
        append(event.type, event.data);
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

// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { EmbeddedGatewayConfig } from "../config";
import type { ConnectorEvent, CreateRunRequest, JsonObject } from "../contract";
import { isJsonObject } from "../json";
import { type Connector, ConnectorError, connectorCapabilities } from "./base";
import {
  JsonWebSocket,
  type WebSocketFactory,
  WebSocketTransportError,
  WebSocketTransportTimeoutError,
} from "./websocket";
import { createHmac, randomUUID } from "node:crypto";

const VSS_WORKSPACE_SKILL_INSTRUCTIONS = `VSS capability contract:
VSS skills are installed in the current OpenClaw workspace, not in OpenClaw's
bundled installation. For a VSS request, read the matching skill from its exact
workspace path listed by OpenClaw (normally ./skills/<skill-name>/SKILL.md).
Never guess a path below OpenClaw's node_modules directory. Follow the selected
skill's bundled runner or recipe exactly.`;

const PROTOCOL_VERSION = 4;
const REQUESTED_SCOPES = ["operator.read", "operator.write"];
const CLIENT_CAPABILITIES = ["tool-events", "session-scoped-events"];

interface ActiveRun {
  socket: JsonWebSocket;
  sessionKey: string;
  upstreamRunId: string;
}

class HandshakeRejected extends Error {
  constructor(readonly detail: unknown) {
    super("OpenClaw rejected the connection");
  }
}

interface NormalizationState {
  sessionKey: string;
  upstreamRunId: string;
  startedTools: Set<string>;
  completedTools: Set<string>;
  toolNames: Map<string, string>;
  sawText: boolean;
}

interface NormalizedFrame {
  events: ConnectorEvent[];
  terminal: boolean;
}

export class OpenClawConnector implements Connector {
  readonly protocol = "openclaw-ws";
  readonly capabilities = connectorCapabilities(this.protocol, {
    tool_events: "native",
    cancellation: "native",
  });
  private readonly endpoint: string;
  private readonly activeRuns = new Map<string, ActiveRun>();

  constructor(
    private readonly config: EmbeddedGatewayConfig,
    private readonly webSocketFactory?: WebSocketFactory
  ) {
    this.endpoint = `${config.backendUrl}${config.backendPath}`;
  }

  private connectParams(): JsonObject {
    const params: JsonObject = {
      minProtocol: PROTOCOL_VERSION,
      maxProtocol: PROTOCOL_VERSION,
      client: {
        id: "gateway-client",
        version: "vss-next-agent-adapter/1.0",
        platform: "linux",
        mode: "backend",
        deviceFamily: "server",
      },
      role: "operator",
      scopes: REQUESTED_SCOPES,
      caps: CLIENT_CAPABILITIES,
      commands: [],
      permissions: {},
      locale: "en-US",
      userAgent: "vss-next-agent-adapter/1.0",
    };
    if (this.config.backendToken) {
      params.auth = { token: this.config.backendToken };
    }
    return params;
  }

  private async receive(
    socket: JsonWebSocket,
    signal: AbortSignal
  ): Promise<JsonObject> {
    try {
      return await socket.receive(this.config.requestTimeoutMs, signal);
    } catch (error) {
      if (signal.aborted) throw error;
      if (error instanceof WebSocketTransportTimeoutError) {
        throw new ConnectorError(
          "OpenClaw Gateway timed out",
          "backend_timeout",
          true
        );
      }
      throw new ConnectorError(
        "OpenClaw Gateway stream ended unexpectedly",
        "backend_stream_error",
        true
      );
    }
  }

  private request(
    socket: JsonWebSocket,
    method: string,
    params: JsonObject
  ): string {
    const id = randomUUID();
    try {
      socket.send({ type: "req", id, method, params });
    } catch (error) {
      throw new ConnectorError(
        "OpenClaw Gateway stream ended unexpectedly",
        "backend_stream_error",
        true
      );
    }
    return id;
  }

  private async awaitResponse(
    socket: JsonWebSocket,
    requestId: string,
    signal: AbortSignal,
    pendingEvents?: JsonObject[]
  ): Promise<JsonObject> {
    while (true) {
      const frame = await this.receive(socket, signal);
      if (frame.type === "event") {
        pendingEvents?.push(frame);
        continue;
      }
      if (frame.type !== "res" || frame.id !== requestId) continue;
      if (frame.ok !== true) throw new HandshakeRejected(frame.error);
      if (!isJsonObject(frame.payload)) {
        throw new ConnectorError(
          "OpenClaw Gateway returned an invalid RPC response",
          "invalid_backend_response"
        );
      }
      return frame.payload;
    }
  }

  private static handshakeError(error: unknown): ConnectorError {
    const details = isJsonObject(error) ? error.details : undefined;
    const detailCode = isJsonObject(details) ? details.code : undefined;
    if (
      [
        "PAIRING_REQUIRED",
        "DEVICE_IDENTITY_REQUIRED",
        "CONTROL_UI_DEVICE_IDENTITY_REQUIRED",
      ].includes(String(detailCode))
    ) {
      return new ConnectorError(
        "OpenClaw requires device identity for this connection; expose the Gateway to this adapter over a trusted private route and use its shared gateway token",
        "backend_auth_error"
      );
    }
    if (
      ["AUTH_TOKEN_MISMATCH", "AUTH_SCOPE_MISMATCH"].includes(
        String(detailCode)
      )
    ) {
      return new ConnectorError(
        "OpenClaw Gateway authentication failed",
        "backend_auth_error"
      );
    }
    return new ConnectorError(
      "OpenClaw Gateway rejected the connection",
      "backend_handshake_error"
    );
  }

  private async connect(signal: AbortSignal): Promise<JsonWebSocket> {
    let socket: JsonWebSocket;
    try {
      socket = await JsonWebSocket.connect(
        this.endpoint,
        Math.min(this.config.requestTimeoutMs, 15_000),
        this.webSocketFactory
      );
    } catch (error) {
      if (error instanceof WebSocketTransportError) {
        throw new ConnectorError(
          "OpenClaw Gateway is unreachable",
          "backend_unreachable",
          true
        );
      }
      throw error;
    }
    try {
      const challenge = await this.receive(socket, signal);
      if (
        challenge.type !== "event" ||
        challenge.event !== "connect.challenge" ||
        !isJsonObject(challenge.payload)
      ) {
        throw new ConnectorError(
          "OpenClaw Gateway did not send a connect challenge",
          "invalid_backend_handshake"
        );
      }
      const nonce = challenge.payload.nonce;
      const signedAt = challenge.payload.ts;
      if (
        typeof nonce !== "string" ||
        !nonce ||
        nonce.length > 4_096 ||
        typeof signedAt !== "number" ||
        !Number.isSafeInteger(signedAt) ||
        signedAt < 0
      ) {
        throw new ConnectorError(
          "OpenClaw Gateway sent an invalid connect challenge",
          "invalid_backend_handshake"
        );
      }
      const connectId = this.request(socket, "connect", this.connectParams());
      let hello: JsonObject;
      try {
        hello = await this.awaitResponse(socket, connectId, signal);
      } catch (error) {
        if (error instanceof HandshakeRejected) {
          throw OpenClawConnector.handshakeError(error.detail);
        }
        throw error;
      }
      if (hello.type !== "hello-ok" || hello.protocol !== PROTOCOL_VERSION) {
        throw new ConnectorError(
          "OpenClaw Gateway negotiated an unsupported protocol",
          "unsupported_backend_protocol"
        );
      }
      if (!isJsonObject(hello.auth) || hello.auth.role !== "operator") {
        throw new ConnectorError(
          "OpenClaw Gateway did not grant the operator role",
          "backend_scope_error"
        );
      }
      const scopes = hello.auth.scopes;
      if (
        !Array.isArray(scopes) ||
        scopes.some((scope) => typeof scope !== "string") ||
        (scopes.length > 0 &&
          REQUESTED_SCOPES.some((scope) => !scopes.includes(scope)))
      ) {
        throw new ConnectorError(
          "OpenClaw Gateway did not grant chat read/write scopes",
          "backend_scope_error"
        );
      }
      if (!isJsonObject(hello.features)) {
        throw new ConnectorError(
          "OpenClaw Gateway lacks required chat or tool-event capabilities",
          "unsupported_backend_protocol"
        );
      }
      const methods = hello.features.methods;
      const events = hello.features.events;
      if (
        !Array.isArray(methods) ||
        methods.some((method) => typeof method !== "string") ||
        !methods.includes("chat.send") ||
        !methods.includes("chat.abort") ||
        !Array.isArray(events) ||
        events.some((event) => typeof event !== "string") ||
        !events.includes("chat") ||
        (!events.includes("agent") && !events.includes("session.tool"))
      ) {
        throw new ConnectorError(
          "OpenClaw Gateway lacks required chat or tool-event capabilities",
          "unsupported_backend_protocol"
        );
      }
      return socket;
    } catch (error) {
      socket.close();
      throw error;
    }
  }

  private sessionKey(threadId: string): string {
    const secret = this.config.backendToken || "vss-next-agent-adapter";
    const digest = createHmac("sha256", secret)
      .update(`vss-ui:${threadId}`)
      .digest("hex")
      .slice(0, 40);
    return `agent:main:vss-ui-${digest}`;
  }

  private message(request: CreateRunRequest): string {
    if (
      !this.config.vssCapabilities &&
      request.instructions === undefined &&
      request.input.length === 1 &&
      request.input[0].role === "user"
    ) {
      return request.input[0].content;
    }
    const parts: string[] = [];
    if (this.config.vssCapabilities) {
      parts.push(VSS_WORKSPACE_SKILL_INSTRUCTIONS);
    }
    if (request.instructions) {
      parts.push(`VSS UI instructions:\n${request.instructions}`);
    }
    for (const message of request.input) {
      parts.push(
        `${message.role[0].toUpperCase()}${message.role.slice(1)}:\n${
          message.content
        }`
      );
    }
    return parts.join("\n\n");
  }

  private static safeIdentifier(value: unknown, fallback: string): string {
    if (typeof value !== "string") return fallback;
    const normalized = value.trim();
    return normalized && normalized.length <= 256 && !/\p{Cc}/u.test(normalized)
      ? normalized
      : fallback;
  }

  private static finalText(payload: JsonObject): string | undefined {
    if (!isJsonObject(payload.message)) return undefined;
    const content = payload.message.content;
    if (typeof content === "string") return content;
    if (!Array.isArray(content)) return undefined;
    const text = content
      .filter(isJsonObject)
      .map((item) => (typeof item.text === "string" ? item.text : ""))
      .join("");
    return text || undefined;
  }

  normalizeEvent(
    frame: JsonObject,
    state: NormalizationState
  ): NormalizedFrame {
    if (frame.type !== "event" || !isJsonObject(frame.payload)) {
      return { events: [], terminal: false };
    }
    const payload = frame.payload;
    if (payload.sessionKey !== state.sessionKey) {
      return { events: [], terminal: false };
    }
    if (
      typeof payload.runId === "string" &&
      payload.runId !== state.upstreamRunId
    ) {
      return { events: [], terminal: false };
    }
    if (frame.event === "chat") {
      if (
        payload.state === "delta" &&
        typeof payload.deltaText === "string" &&
        payload.deltaText
      ) {
        state.sawText = true;
        return {
          events: [
            { type: "message.delta", data: { delta: payload.deltaText } },
          ],
          terminal: false,
        };
      }
      if (payload.state === "final") {
        const finalText = state.sawText
          ? undefined
          : OpenClawConnector.finalText(payload);
        return {
          events: finalText
            ? [{ type: "message.delta", data: { delta: finalText } }]
            : [],
          terminal: true,
        };
      }
      if (payload.state === "error" || payload.state === "failed") {
        throw new ConnectorError(
          "OpenClaw agent run failed",
          "backend_run_failed"
        );
      }
      if (payload.state === "aborted" || payload.state === "cancelled") {
        throw new ConnectorError(
          "OpenClaw agent run was aborted",
          "backend_run_aborted"
        );
      }
      return { events: [], terminal: false };
    }

    let toolData: unknown;
    if (frame.event === "agent" && payload.stream === "tool") {
      toolData = payload.data;
    } else if (frame.event === "session.tool") {
      toolData = payload.data ?? payload;
    } else {
      return { events: [], terminal: false };
    }
    if (!isJsonObject(toolData)) return { events: [], terminal: false };

    const fallbackId = `tool-${String(payload.seq ?? "unknown")}`;
    const toolCallId = OpenClawConnector.safeIdentifier(
      toolData.toolCallId || toolData.id,
      fallbackId
    );
    const name = OpenClawConnector.safeIdentifier(
      toolData.name || toolData.tool,
      state.toolNames.get(toolCallId) || "Agent tool"
    );
    state.toolNames.set(toolCallId, name);
    const phase = String(
      toolData.phase || toolData.status || "start"
    ).toLowerCase();
    const events: ConnectorEvent[] = [];
    if (["start", "started", "running", "in_progress"].includes(phase)) {
      if (!state.startedTools.has(toolCallId)) {
        state.startedTools.add(toolCallId);
        events.push({
          type: "tool.started",
          data: {
            tool_call_id: toolCallId,
            name,
            payload: "Running",
          },
        });
      }
      return { events, terminal: false };
    }
    if (["update", "delta", "progress"].includes(phase)) {
      return { events: [], terminal: false };
    }
    if (
      !["result", "complete", "completed", "error", "failed"].includes(phase) ||
      state.completedTools.has(toolCallId)
    ) {
      return { events: [], terminal: false };
    }
    state.completedTools.add(toolCallId);
    if (!state.startedTools.has(toolCallId)) {
      state.startedTools.add(toolCallId);
      events.push({
        type: "tool.started",
        data: { tool_call_id: toolCallId, name, payload: "Running" },
      });
    }
    if (["error", "failed"].includes(phase) || toolData.isError === true) {
      events.push({
        type: "tool.failed",
        data: {
          tool_call_id: toolCallId,
          name,
          error: "Tool failed in OpenClaw",
        },
      });
    } else {
      const data: JsonObject = {
        tool_call_id: toolCallId,
        name,
        payload: "Completed",
      };
      if (toolData.result !== undefined)
        data._artifact_source = toolData.result;
      events.push({ type: "tool.completed", data });
    }
    return { events, terminal: false };
  }

  async *run(
    request: CreateRunRequest,
    runId: string,
    signal: AbortSignal
  ): AsyncGenerator<ConnectorEvent> {
    const socket = await this.connect(signal);
    const sessionKey = this.sessionKey(request.threadId);
    this.activeRuns.set(runId, { socket, sessionKey, upstreamRunId: runId });
    try {
      const pendingEvents: JsonObject[] = [];
      const sendId = this.request(socket, "chat.send", {
        sessionKey,
        message: this.message(request),
        idempotencyKey: runId,
      });
      let accepted: JsonObject;
      try {
        accepted = await this.awaitResponse(
          socket,
          sendId,
          signal,
          pendingEvents
        );
      } catch (error) {
        if (error instanceof HandshakeRejected) {
          throw new ConnectorError(
            "OpenClaw rejected the chat request",
            "backend_request_rejected"
          );
        }
        throw error;
      }
      const upstreamRunId = OpenClawConnector.safeIdentifier(
        accepted.runId,
        runId
      );
      if (
        accepted.status !== undefined &&
        accepted.status !== "started" &&
        accepted.status !== "accepted"
      ) {
        throw new ConnectorError(
          "OpenClaw did not start the agent run",
          "backend_request_rejected"
        );
      }
      this.activeRuns.set(runId, { socket, sessionKey, upstreamRunId });
      const state: NormalizationState = {
        sessionKey,
        upstreamRunId,
        startedTools: new Set(),
        completedTools: new Set(),
        toolNames: new Map(),
        sawText: false,
      };
      while (!signal.aborted) {
        const frame =
          pendingEvents.shift() ?? (await this.receive(socket, signal));
        const normalized = this.normalizeEvent(frame, state);
        for (const event of normalized.events) yield event;
        if (normalized.terminal) return;
      }
    } catch (error) {
      if (signal.aborted) return;
      throw error;
    } finally {
      if (this.activeRuns.get(runId)?.socket === socket) {
        this.activeRuns.delete(runId);
      }
      socket.close();
    }
  }

  cancel(runId: string): void {
    const active = this.activeRuns.get(runId);
    if (!active) return;
    try {
      this.request(active.socket, "chat.abort", {
        sessionKey: active.sessionKey,
        runId: active.upstreamRunId,
      });
    } catch {
      // Closing the socket remains a prompt cancellation fallback.
    } finally {
      active.socket.close();
    }
  }
}

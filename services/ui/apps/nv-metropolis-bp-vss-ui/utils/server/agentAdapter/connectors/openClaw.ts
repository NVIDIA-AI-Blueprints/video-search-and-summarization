// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { AgentAdapterConfig } from "../config";
import type {
  ConnectorEvent,
  CreateRunRequest,
  InteractionResponse,
  JsonObject,
} from "../contract";
import { isJsonObject } from "../json";
import { type Connector, ConnectorError, connectorCapabilities } from "./base";
import {
  JsonWebSocket,
  type WebSocketFactory,
  WebSocketTransportError,
  WebSocketTransportTimeoutError,
} from "./websocket";
import { createHmac, randomUUID } from "node:crypto";

const PROTOCOL_VERSION = 4;
const CHAT_SCOPES = ["operator.read", "operator.write"];
const QUESTION_SCOPE = "operator.questions";
const CLIENT_CAPABILITIES = ["tool-events", "session-scoped-events"];
const QUESTION_ID = /^[a-z][a-z0-9_]*$/u;
const MAX_QUESTION_TEXT_LENGTH = 100_000;

const asString = (value: unknown): string | undefined =>
  typeof value === "string" ? value : undefined;

const sequenceText = (value: unknown): string => {
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) {
    return value.toString();
  }
  return "unknown";
};

interface ActiveRun {
  socket: JsonWebSocket;
  sessionKey: string;
  upstreamRunId: string;
  pendingQuestions: Map<string, PendingQuestion>;
  responseWaiters: Map<string, ResponseWaiter>;
}

interface ResponseWaiter {
  resolve: (payload: JsonObject) => void;
  reject: (error: Error) => void;
  timeout: ReturnType<typeof setTimeout>;
}

interface NormalizedQuestion {
  question_id: string;
  header: string;
  prompt: string;
  options: Array<{ label: string; description?: string }>;
  multi_select: boolean;
  allow_other: boolean;
}

interface PendingQuestion {
  questions: NormalizedQuestion[];
  expiresAtMs: number;
}

class RpcRejected extends Error {
  constructor(readonly detail: unknown) {
    super("OpenClaw rejected an RPC request");
  }
}

interface NormalizationState {
  sessionKey: string;
  upstreamRunId: string;
  pendingQuestions: Map<string, PendingQuestion>;
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
  readonly capabilities: JsonObject;
  private readonly endpoint: string;
  private readonly activeRuns = new Map<string, ActiveRun>();

  constructor(
    private readonly config: AgentAdapterConfig,
    private readonly webSocketFactory?: WebSocketFactory,
  ) {
    this.endpoint = `${config.backendUrl}${config.backendPath}`;
    this.capabilities = connectorCapabilities(this.protocol, {
      tool_events: "native",
      cancellation: "native",
      interactions: config.interactionsEnabled,
    });
  }

  private requestedScopes(): string[] {
    return this.config.interactionsEnabled
      ? [...CHAT_SCOPES, QUESTION_SCOPE]
      : CHAT_SCOPES;
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
      scopes: this.requestedScopes(),
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
    signal: AbortSignal,
  ): Promise<JsonObject> {
    try {
      return await socket.receive(this.config.requestTimeoutMs, signal);
    } catch (error) {
      if (signal.aborted) throw error;
      if (error instanceof WebSocketTransportTimeoutError) {
        throw new ConnectorError(
          "OpenClaw Gateway timed out",
          "backend_timeout",
          true,
        );
      }
      throw new ConnectorError(
        "OpenClaw Gateway stream ended unexpectedly",
        "backend_stream_error",
        true,
        { cause: error },
      );
    }
  }

  private request(
    socket: JsonWebSocket,
    method: string,
    params: JsonObject,
  ): string {
    const id = randomUUID();
    try {
      socket.send({ type: "req", id, method, params });
    } catch (error) {
      throw new ConnectorError(
        "OpenClaw Gateway stream ended unexpectedly",
        "backend_stream_error",
        true,
        { cause: error },
      );
    }
    return id;
  }

  private async awaitResponse(
    socket: JsonWebSocket,
    requestId: string,
    signal: AbortSignal,
    pendingEvents?: JsonObject[],
  ): Promise<JsonObject> {
    while (true) {
      const frame = await this.receive(socket, signal);
      if (frame.type === "event") {
        pendingEvents?.push(frame);
        continue;
      }
      if (frame.type !== "res" || frame.id !== requestId) continue;
      if (frame.ok !== true) throw new RpcRejected(frame.error);
      if (!isJsonObject(frame.payload)) {
        throw new ConnectorError(
          "OpenClaw Gateway returned an invalid RPC response",
          "invalid_backend_response",
        );
      }
      return frame.payload;
    }
  }

  private settleResponse(active: ActiveRun, frame: JsonObject): boolean {
    if (frame.type !== "res" || typeof frame.id !== "string") return false;
    const waiter = active.responseWaiters.get(frame.id);
    if (!waiter) return false;
    active.responseWaiters.delete(frame.id);
    clearTimeout(waiter.timeout);
    if (frame.ok !== true) {
      waiter.reject(new RpcRejected(frame.error));
    } else if (!isJsonObject(frame.payload)) {
      waiter.reject(
        new ConnectorError(
          "OpenClaw Gateway returned an invalid RPC response",
          "invalid_backend_response",
        ),
      );
    } else {
      waiter.resolve(frame.payload);
    }
    return true;
  }

  private requestDuringRun(
    active: ActiveRun,
    method: string,
    params: JsonObject,
  ): Promise<JsonObject> {
    const id = randomUUID();
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(
        () => {
          active.responseWaiters.delete(id);
          reject(
            new ConnectorError(
              "OpenClaw Gateway timed out while accepting an interaction response",
              "backend_timeout",
              true,
            ),
          );
        },
        Math.min(this.config.requestTimeoutMs, 30_000),
      );
      timeout.unref?.();
      active.responseWaiters.set(id, { resolve, reject, timeout });
      try {
        active.socket.send({ type: "req", id, method, params });
      } catch (error) {
        active.responseWaiters.delete(id);
        clearTimeout(timeout);
        reject(
          new ConnectorError(
            "OpenClaw Gateway stream ended unexpectedly",
            "backend_stream_error",
            true,
            {
              cause: error,
            },
          ),
        );
      }
    });
  }

  private rejectResponseWaiters(active: ActiveRun): void {
    for (const waiter of active.responseWaiters.values()) {
      clearTimeout(waiter.timeout);
      waiter.reject(
        new ConnectorError(
          "OpenClaw Gateway stream ended before accepting the interaction response",
          "backend_stream_error",
          true,
        ),
      );
    }
    active.responseWaiters.clear();
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
        "backend_auth_error",
      );
    }
    if (
      ["AUTH_TOKEN_MISMATCH", "AUTH_SCOPE_MISMATCH"].includes(
        String(detailCode),
      )
    ) {
      return new ConnectorError(
        "OpenClaw Gateway authentication failed",
        "backend_auth_error",
      );
    }
    return new ConnectorError(
      "OpenClaw Gateway rejected the connection",
      "backend_handshake_error",
    );
  }

  private async connect(signal: AbortSignal): Promise<JsonWebSocket> {
    let socket: JsonWebSocket;
    try {
      socket = await JsonWebSocket.connect(
        this.endpoint,
        Math.min(this.config.requestTimeoutMs, 15_000),
        this.webSocketFactory,
      );
    } catch (error) {
      if (error instanceof WebSocketTransportError) {
        throw new ConnectorError(
          "OpenClaw Gateway is unreachable",
          "backend_unreachable",
          true,
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
          "invalid_backend_handshake",
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
          "invalid_backend_handshake",
        );
      }
      const connectId = this.request(socket, "connect", this.connectParams());
      let hello: JsonObject;
      try {
        hello = await this.awaitResponse(socket, connectId, signal);
      } catch (error) {
        if (error instanceof RpcRejected) {
          throw OpenClawConnector.handshakeError(error.detail);
        }
        throw error;
      }
      if (hello.type !== "hello-ok" || hello.protocol !== PROTOCOL_VERSION) {
        throw new ConnectorError(
          "OpenClaw Gateway negotiated an unsupported protocol",
          "unsupported_backend_protocol",
        );
      }
      if (!isJsonObject(hello.auth) || hello.auth.role !== "operator") {
        throw new ConnectorError(
          "OpenClaw Gateway did not grant the operator role",
          "backend_scope_error",
        );
      }
      const scopes = hello.auth.scopes;
      if (
        !Array.isArray(scopes) ||
        scopes.some((scope) => typeof scope !== "string") ||
        (scopes.length > 0 &&
          CHAT_SCOPES.some((scope) => !scopes.includes(scope)))
      ) {
        throw new ConnectorError(
          "OpenClaw Gateway did not grant chat read/write scopes",
          "backend_scope_error",
        );
      }
      if (
        this.config.interactionsEnabled &&
        (!scopes.length || !scopes.includes(QUESTION_SCOPE))
      ) {
        throw new ConnectorError(
          "OpenClaw Gateway did not grant operator.questions; structured questions require OpenClaw 2026.8.1 or newer",
          "unsupported_backend_interactions",
        );
      }
      if (!isJsonObject(hello.features)) {
        throw new ConnectorError(
          "OpenClaw Gateway lacks required chat or tool-event capabilities",
          "unsupported_backend_protocol",
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
          "unsupported_backend_protocol",
        );
      }
      if (
        this.config.interactionsEnabled &&
        (!methods.includes("question.resolve") ||
          !events.includes("question.requested") ||
          !events.includes("question.resolved"))
      ) {
        throw new ConnectorError(
          "OpenClaw Gateway lacks structured-question capabilities; OpenClaw 2026.8.1 or newer is required",
          "unsupported_backend_interactions",
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
      request.instructions === undefined &&
      request.input.length === 1 &&
      request.input[0].role === "user"
    ) {
      return request.input[0].content;
    }
    const parts: string[] = [];
    if (request.instructions) {
      parts.push(`VSS UI instructions:\n${request.instructions}`);
    }
    for (const message of request.input) {
      parts.push(
        `${message.role[0].toUpperCase()}${message.role.slice(1)}:\n${
          message.content
        }`,
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

  private static boundedString(
    value: unknown,
    maximum = MAX_QUESTION_TEXT_LENGTH,
    allowEmpty = false,
  ): string | undefined {
    if (typeof value !== "string") return undefined;
    const normalized = value.trim();
    if ((!allowEmpty && !normalized) || normalized.length > maximum) {
      return undefined;
    }
    return normalized;
  }

  private static normalizeQuestion(value: unknown): NormalizedQuestion | null {
    if (!isJsonObject(value)) return null;
    const questionId = OpenClawConnector.boundedString(value.questionId, 256);
    const header = OpenClawConnector.boundedString(value.header, 12, true);
    const prompt = OpenClawConnector.boundedString(value.question);
    if (
      !questionId ||
      !QUESTION_ID.test(questionId) ||
      header === undefined ||
      !prompt
    ) {
      return null;
    }
    if (
      !Array.isArray(value.options) ||
      value.options.length === 1 ||
      value.options.length > 4
    ) {
      return null;
    }
    const options: NormalizedQuestion["options"] = [];
    for (const option of value.options) {
      if (!isJsonObject(option)) return null;
      const label = OpenClawConnector.boundedString(option.label);
      const description = OpenClawConnector.boundedString(
        option.description,
        MAX_QUESTION_TEXT_LENGTH,
        true,
      );
      if (
        !label ||
        (option.description !== undefined && description === undefined)
      ) {
        return null;
      }
      options.push({ label, ...(description ? { description } : {}) });
    }
    if (
      new Set(options.map((option) => option.label.trim().toLowerCase()))
        .size !== options.length
    ) {
      return null;
    }
    return {
      question_id: questionId,
      header,
      prompt,
      options,
      multi_select: value.multiSelect === true,
      allow_other: value.isOther === true,
    };
  }

  private static questionBelongsToRun(
    payload: JsonObject,
    state: NormalizationState,
  ): boolean {
    const sessionKey = asString(payload.sessionKey);
    const upstreamRunId = asString(payload.runId);
    if (!sessionKey && !upstreamRunId) return false;
    if (sessionKey && sessionKey !== state.sessionKey) return false;
    if (upstreamRunId && upstreamRunId !== state.upstreamRunId) return false;
    return true;
  }

  private static normalizeQuestionRecord(
    payload: JsonObject,
    state: NormalizationState,
  ): {
    interactionId: string;
    pending: PendingQuestion;
    createdAtMs: number;
  } | null {
    if (payload.status !== "pending") return null;
    const interactionId = OpenClawConnector.boundedString(payload.id, 256);
    const createdAtMs = payload.createdAtMs;
    const expiresAtMs = payload.expiresAtMs;
    if (
      !interactionId ||
      !Array.isArray(payload.questions) ||
      payload.questions.length < 1 ||
      payload.questions.length > 3 ||
      typeof createdAtMs !== "number" ||
      !Number.isSafeInteger(createdAtMs) ||
      createdAtMs < 0 ||
      typeof expiresAtMs !== "number" ||
      !Number.isSafeInteger(expiresAtMs) ||
      expiresAtMs <= createdAtMs
    ) {
      return null;
    }
    if (!OpenClawConnector.questionBelongsToRun(payload, state)) return null;
    // OpenClaw secret questions write into its secret store and carry an
    // explicit host-consent contract. The VSS chat API intentionally does not
    // proxy that privileged flow until it can preserve the complete consent
    // metadata and secret-store response fields end to end.
    if (
      payload.questions.some(
        (question) => isJsonObject(question) && question.isSecret === true,
      )
    ) {
      throw new ConnectorError(
        "OpenClaw secret-store questions are not supported by the VSS UI",
        "unsupported_backend_interaction",
      );
    }
    const questions = payload.questions.map(
      OpenClawConnector.normalizeQuestion,
    );
    if (questions.some((question) => question === null)) return null;
    const normalized = questions as NormalizedQuestion[];
    if (
      new Set(normalized.map((question) => question.question_id)).size !==
      normalized.length
    ) {
      return null;
    }
    return {
      interactionId,
      pending: { questions: normalized, expiresAtMs },
      createdAtMs,
    };
  }

  normalizeEvent(
    frame: JsonObject,
    state: NormalizationState,
  ): NormalizedFrame {
    if (frame.type !== "event" || !isJsonObject(frame.payload)) {
      return { events: [], terminal: false };
    }
    const payload = frame.payload;
    if (frame.event === "question.requested") {
      if (!this.config.interactionsEnabled) {
        return { events: [], terminal: false };
      }
      const record = OpenClawConnector.normalizeQuestionRecord(payload, state);
      if (!record || state.pendingQuestions.has(record.interactionId)) {
        return { events: [], terminal: false };
      }
      state.pendingQuestions.set(record.interactionId, record.pending);
      return {
        events: [
          {
            type: "interaction.required",
            data: {
              interaction_id: record.interactionId,
              kind: "questions",
              questions: record.pending.questions,
              created_at_ms: record.createdAtMs,
              expires_at_ms: record.pending.expiresAtMs,
            },
          },
        ],
        terminal: false,
      };
    }
    if (frame.event === "question.resolved") {
      if (!this.config.interactionsEnabled) {
        return { events: [], terminal: false };
      }
      const interactionId = OpenClawConnector.boundedString(payload.id, 256);
      if (
        !interactionId ||
        !state.pendingQuestions.has(interactionId) ||
        !["answered", "cancelled", "expired"].includes(String(payload.status))
      ) {
        return { events: [], terminal: false };
      }
      state.pendingQuestions.delete(interactionId);
      return {
        events: [
          {
            type: "interaction.resolved",
            data: { interaction_id: interactionId, status: payload.status },
          },
        ],
        terminal: false,
      };
    }
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
          "backend_run_failed",
        );
      }
      if (payload.state === "aborted" || payload.state === "cancelled") {
        throw new ConnectorError(
          "OpenClaw agent run was aborted",
          "backend_run_aborted",
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

    const fallbackId = `tool-${sequenceText(payload.seq)}`;
    const toolCallId = OpenClawConnector.safeIdentifier(
      toolData.toolCallId || toolData.id,
      fallbackId,
    );
    const name = OpenClawConnector.safeIdentifier(
      toolData.name || toolData.tool,
      state.toolNames.get(toolCallId) || "Agent tool",
    );
    state.toolNames.set(toolCallId, name);
    const phase = (
      asString(toolData.phase) ??
      asString(toolData.status) ??
      "start"
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
    signal: AbortSignal,
  ): AsyncGenerator<ConnectorEvent> {
    const socket = await this.connect(signal);
    const sessionKey = this.sessionKey(request.threadId);
    const active: ActiveRun = {
      socket,
      sessionKey,
      upstreamRunId: runId,
      pendingQuestions: new Map(),
      responseWaiters: new Map(),
    };
    this.activeRuns.set(runId, active);
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
          pendingEvents,
        );
      } catch (error) {
        if (error instanceof RpcRejected) {
          throw new ConnectorError(
            "OpenClaw rejected the chat request",
            "backend_request_rejected",
          );
        }
        throw error;
      }
      const upstreamRunId = OpenClawConnector.safeIdentifier(
        accepted.runId,
        runId,
      );
      if (
        accepted.status !== undefined &&
        accepted.status !== "started" &&
        accepted.status !== "accepted"
      ) {
        throw new ConnectorError(
          "OpenClaw did not start the agent run",
          "backend_request_rejected",
        );
      }
      active.upstreamRunId = upstreamRunId;
      const state: NormalizationState = {
        sessionKey,
        upstreamRunId,
        pendingQuestions: active.pendingQuestions,
        startedTools: new Set(),
        completedTools: new Set(),
        toolNames: new Map(),
        sawText: false,
      };
      while (!signal.aborted) {
        const frame =
          pendingEvents.shift() ?? (await this.receive(socket, signal));
        if (this.settleResponse(active, frame)) continue;
        const normalized = this.normalizeEvent(frame, state);
        for (const event of normalized.events) yield event;
        if (normalized.terminal) return;
      }
    } catch (error) {
      if (signal.aborted) return;
      throw error;
    } finally {
      this.rejectResponseWaiters(active);
      if (this.activeRuns.get(runId)?.socket === socket) {
        this.activeRuns.delete(runId);
      }
      socket.close();
    }
  }

  async respond(runId: string, response: InteractionResponse): Promise<void> {
    if (!this.config.interactionsEnabled) {
      throw new ConnectorError(
        "structured interaction responses are disabled",
        "interaction_not_supported",
      );
    }
    const active = this.activeRuns.get(runId);
    if (!active) {
      throw new ConnectorError(
        "the OpenClaw run is no longer active",
        "interaction_not_pending",
      );
    }
    const pending = active.pendingQuestions.get(response.interactionId);
    if (!pending) {
      throw new ConnectorError(
        "the interaction is no longer pending",
        "interaction_not_pending",
      );
    }
    if (pending.expiresAtMs <= Date.now()) {
      throw new ConnectorError(
        "the interaction has expired",
        "interaction_expired",
      );
    }
    const expectedIds = new Set(
      pending.questions.map((question) => question.question_id),
    );
    const answerIds = Object.keys(response.answers);
    if (
      answerIds.length !== expectedIds.size ||
      answerIds.some((questionId) => !expectedIds.has(questionId))
    ) {
      throw new ConnectorError(
        "the interaction response must answer every pending question exactly once",
        "invalid_interaction_response",
      );
    }
    const normalizedAnswers: Record<string, string[]> = {};
    for (const question of pending.questions) {
      const received = response.answers[question.question_id] ?? [];
      const answers = received.map((answer) => answer.trim());
      if (
        answers.some((answer) => !answer.trim()) ||
        new Set(answers).size !== answers.length
      ) {
        throw new ConnectorError(
          `${question.question_id} contains an empty or duplicate answer`,
          "invalid_interaction_response",
        );
      }
      if (!question.multi_select && answers.length !== 1) {
        throw new ConnectorError(
          `${question.question_id} accepts exactly one answer`,
          "invalid_interaction_response",
        );
      }
      const labels = new Set(question.options.map((option) => option.label));
      if (
        labels.size > 0 &&
        !question.allow_other &&
        answers.some((answer) => !labels.has(answer))
      ) {
        throw new ConnectorError(
          `${question.question_id} requires a declared option`,
          "invalid_interaction_response",
        );
      }
      normalizedAnswers[question.question_id] = answers;
    }
    let result: JsonObject;
    try {
      // OpenClaw 2026.8.1 validates a closed payload: only the question id,
      // answer envelope, and optional resolver identity are accepted here.
      result = await this.requestDuringRun(active, "question.resolve", {
        id: response.interactionId,
        answers: { answers: normalizedAnswers },
        resolvedBy: "vss-ui",
      });
    } catch (error) {
      if (error instanceof RpcRejected) {
        throw new ConnectorError(
          "OpenClaw rejected the interaction response",
          "backend_interaction_rejected",
        );
      }
      throw error;
    }
    if (result.status !== "answered") {
      throw new ConnectorError(
        "OpenClaw did not accept the interaction response",
        "backend_interaction_rejected",
      );
    }
    active.pendingQuestions.delete(response.interactionId);
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

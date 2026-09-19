// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { AgentAdapterConfig } from "../config";
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

const PROTOCOL_VERSION = 4;
const REQUESTED_SCOPES = ["operator.read", "operator.write"];
const CLIENT_CAPABILITIES = ["tool-events", "session-scoped-events"];
// Keep base64 plus the artifact envelope below the adapter's 1 MB event limit.
const MAX_MANAGED_IMAGE_BYTES = 700_000;
const MAX_MANAGED_IMAGE_BLOCKS = 8;
const MANAGED_IMAGE_RECOVERY_TIMEOUT_MS = 5_000;
const MANAGED_IMAGE_ROUTE =
  /^\/api\/chat\/media\/outgoing\/([^/]+)\/[0-9a-f-]+\/full$/iu;
const MANAGED_ALT_SUFFIX =
  /---[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?=\.[a-z0-9]{1,10}$)/iu;
const MANAGED_IMAGE_MIME_TYPES = new Set([
  "image/bmp",
  "image/gif",
  "image/jpeg",
  "image/png",
  "image/webp",
]);
const TOOL_START_PHASES = new Set([
  "start",
  "started",
  "running",
  "in_progress",
]);
const TOOL_PROGRESS_PHASES = new Set(["update", "delta", "progress"]);
const TOOL_END_PHASES = new Set([
  "result",
  "complete",
  "completed",
  "error",
  "failed",
]);
const TOOL_FAILURE_PHASES = new Set(["error", "failed"]);

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
  imageSources: Map<string, { source: string; mimeType: string }>;
  materializedImageSources: Set<string>;
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
    private readonly config: AgentAdapterConfig,
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
        true,
        { cause: error }
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
        true,
        { cause: error }
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

  private static managedImageBlocks(
    payload: JsonObject,
    sessionKey: string
  ): JsonObject[] {
    if (!isJsonObject(payload.message)) return [];
    const content = payload.message.content;
    if (!Array.isArray(content)) return [];
    const images: JsonObject[] = [];
    for (const item of content) {
      if (
        images.length >= MAX_MANAGED_IMAGE_BLOCKS ||
        !isJsonObject(item) ||
        item.type !== "image" ||
        typeof item.url !== "string" ||
        typeof item.alt !== "string" ||
        typeof item.mimeType !== "string" ||
        !MANAGED_IMAGE_MIME_TYPES.has(item.mimeType)
      ) {
        continue;
      }
      const match = MANAGED_IMAGE_ROUTE.exec(item.url);
      if (!match) continue;
      let imageSessionKey: string;
      try {
        imageSessionKey = decodeURIComponent(match[1]);
      } catch {
        continue;
      }
      if (imageSessionKey === sessionKey) images.push(item);
    }
    return images;
  }

  private static managedImageSourceNames(image: JsonObject): string[] {
    if (typeof image.alt !== "string") return [];
    const alt = image.alt.trim();
    if (
      !alt ||
      alt.length > 256 ||
      alt.includes("/") ||
      alt.includes("\\") ||
      /\p{Cc}/u.test(alt)
    ) {
      return [];
    }
    const original = alt.replace(MANAGED_ALT_SUFFIX, "");
    return original === alt ? [alt] : [original, alt];
  }

  private static toolImageSource(
    toolData: JsonObject
  ): { source: string; mimeType: string } | undefined {
    const name = asString(toolData.name || toolData.tool)?.toLowerCase();
    const outerArgs = isJsonObject(toolData.args) ? toolData.args : undefined;
    let readArgs: JsonObject | undefined;
    if (name === "read") {
      readArgs = outerArgs;
    } else if (
      name === "tool_call" &&
      typeof outerArgs?.id === "string" &&
      /(?:^|:)read$/u.test(outerArgs.id) &&
      isJsonObject(outerArgs.args)
    ) {
      readArgs = outerArgs.args;
    }
    if (!readArgs || typeof readArgs.path !== "string") return undefined;
    const source = readArgs.path.trim();
    const workspacePrefix = "/sandbox/.openclaw/workspace/";
    if (
      !source.startsWith(workspacePrefix) ||
      source.length > 1_024 ||
      source.includes("\\") ||
      /\p{Cc}/u.test(source)
    ) {
      return undefined;
    }
    const relative = source.slice(workspacePrefix.length);
    if (
      !relative ||
      relative
        .split("/")
        .some((segment) => !segment || segment === "." || segment === "..")
    ) {
      return undefined;
    }
    const extension = /\.([a-z0-9]+)$/iu.exec(relative)?.[1]?.toLowerCase();
    const mimeType = extension
      ? {
          bmp: "image/bmp",
          gif: "image/gif",
          jpeg: "image/jpeg",
          jpg: "image/jpeg",
          png: "image/png",
          webp: "image/webp",
        }[extension]
      : undefined;
    return mimeType ? { source, mimeType } : undefined;
  }

  private backendHttpUrl(pathname: string): URL {
    const url = new URL(this.config.backendUrl);
    url.protocol = url.protocol === "wss:" ? "https:" : "http:";
    const basePath = url.pathname.replace(/\/$/u, "");
    url.pathname = `${basePath}${pathname}`;
    url.search = "";
    url.hash = "";
    return url;
  }

  private backendHeaders(accept: string): Headers {
    const headers = new Headers({ Accept: accept });
    if (this.config.backendToken) {
      headers.set("Authorization", `Bearer ${this.config.backendToken}`);
    }
    return headers;
  }

  private managedImageRecoverySignal(runSignal: AbortSignal): AbortSignal {
    if (runSignal.aborted) return runSignal;
    return AbortSignal.any([
      runSignal,
      AbortSignal.timeout(
        Math.min(
          MANAGED_IMAGE_RECOVERY_TIMEOUT_MS,
          this.config.requestTimeoutMs
        )
      ),
    ]);
  }

  private async readManagedImage(
    source: string,
    expectedMimeType: string,
    alt: string,
    signal: AbortSignal
  ): Promise<JsonObject | undefined> {
    try {
      const metadataUrl = this.backendHttpUrl("/__openclaw__/assistant-media");
      metadataUrl.searchParams.set("source", source);
      metadataUrl.searchParams.set("meta", "1");
      const metadataResponse = await fetch(metadataUrl, {
        headers: this.backendHeaders("application/json"),
        signal,
      });
      if (!metadataResponse.ok) return undefined;
      const metadataText = await metadataResponse.text();
      if (metadataText.length > 4_096) return undefined;
      const metadata: unknown = JSON.parse(metadataText);
      if (
        !isJsonObject(metadata) ||
        metadata.available !== true ||
        typeof metadata.mediaTicket !== "string" ||
        !metadata.mediaTicket ||
        metadata.mediaTicket.length > 4_096
      ) {
        return undefined;
      }

      const mediaUrl = this.backendHttpUrl("/__openclaw__/assistant-media");
      mediaUrl.searchParams.set("source", source);
      mediaUrl.searchParams.set("mediaTicket", metadata.mediaTicket);
      const mediaResponse = await fetch(mediaUrl, {
        headers: this.backendHeaders("image/*"),
        signal,
      });
      const mimeType = mediaResponse.headers
        .get("content-type")
        ?.split(";", 1)[0]
        .trim()
        .toLowerCase();
      const declaredLength = Number(
        mediaResponse.headers.get("content-length") ?? "0"
      );
      if (
        !mediaResponse.ok ||
        !mimeType ||
        mimeType !== expectedMimeType ||
        !MANAGED_IMAGE_MIME_TYPES.has(mimeType) ||
        (Number.isFinite(declaredLength) &&
          declaredLength > MAX_MANAGED_IMAGE_BYTES) ||
        !mediaResponse.body
      ) {
        await mediaResponse.body?.cancel();
        return undefined;
      }

      const chunks: Uint8Array[] = [];
      const reader = mediaResponse.body.getReader();
      let length = 0;
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          length += value.byteLength;
          if (length > MAX_MANAGED_IMAGE_BYTES) {
            await reader.cancel();
            return undefined;
          }
          chunks.push(value);
        }
      } finally {
        reader.releaseLock();
      }
      if (!length) return undefined;
      return {
        type: "image",
        data: Buffer.concat(chunks.map((chunk) => Buffer.from(chunk))).toString(
          "base64"
        ),
        mimeType,
        alt,
      };
    } catch {
      return undefined;
    }
  }

  private async materializeManagedImage(
    image: JsonObject,
    signal: AbortSignal
  ): Promise<JsonObject | undefined> {
    const names = OpenClawConnector.managedImageSourceNames(image);
    if (!names.length || typeof image.mimeType !== "string") return undefined;
    const roots = [
      "/sandbox/.openclaw/workspace",
      "/sandbox/.openclaw/workspace/artifacts",
    ];
    for (const root of roots) {
      for (const name of names) {
        if (signal.aborted) return undefined;
        const source = `${root}/${name}`;
        const materialized = await this.readManagedImage(
          source,
          image.mimeType,
          typeof image.alt === "string" ? image.alt : "VSS snapshot",
          signal
        );
        if (materialized) return materialized;
      }
    }
    return undefined;
  }

  private static emptyNormalizedFrame(): NormalizedFrame {
    return { events: [], terminal: false };
  }

  private static toolData(
    frame: JsonObject,
    payload: JsonObject
  ): JsonObject | undefined {
    let value: unknown;
    if (frame.event === "agent" && payload.stream === "tool") {
      value = payload.data;
    } else if (frame.event === "session.tool") {
      value = payload.data ?? payload;
    }
    return isJsonObject(value) ? value : undefined;
  }

  private normalizeChatDelta(
    payload: JsonObject,
    state: NormalizationState
  ): NormalizedFrame {
    if (typeof payload.deltaText !== "string" || !payload.deltaText) {
      return OpenClawConnector.emptyNormalizedFrame();
    }
    state.sawText = true;
    return {
      events: [{ type: "message.delta", data: { delta: payload.deltaText } }],
      terminal: false,
    };
  }

  private async normalizeFinalChat(
    payload: JsonObject,
    state: NormalizationState,
    signal: AbortSignal
  ): Promise<NormalizedFrame> {
    const finalText = state.sawText
      ? undefined
      : OpenClawConnector.finalText(payload);
    const events: ConnectorEvent[] = finalText
      ? [{ type: "message.delta", data: { delta: finalText } }]
      : [];
    const recoverySignal = this.managedImageRecoverySignal(signal);
    for (const image of OpenClawConnector.managedImageBlocks(
      payload,
      state.sessionKey
    )) {
      if (recoverySignal.aborted) break;
      const source = await this.materializeManagedImage(image, recoverySignal);
      if (source) {
        events.push({ type: "artifact.source", data: { source } });
      }
    }
    return { events, terminal: true };
  }

  private async normalizeChatEvent(
    payload: JsonObject,
    state: NormalizationState,
    signal: AbortSignal
  ): Promise<NormalizedFrame> {
    switch (payload.state) {
      case "delta":
        return this.normalizeChatDelta(payload, state);
      case "final":
        return this.normalizeFinalChat(payload, state, signal);
      case "error":
      case "failed":
        throw new ConnectorError(
          "OpenClaw agent run failed",
          "backend_run_failed"
        );
      case "aborted":
      case "cancelled":
        throw new ConnectorError(
          "OpenClaw agent run was aborted",
          "backend_run_aborted"
        );
      default:
        return OpenClawConnector.emptyNormalizedFrame();
    }
  }

  private static ensureToolStarted(
    events: ConnectorEvent[],
    state: NormalizationState,
    toolCallId: string,
    name: string
  ): void {
    if (state.startedTools.has(toolCallId)) return;
    state.startedTools.add(toolCallId);
    events.push({
      type: "tool.started",
      data: { tool_call_id: toolCallId, name, payload: "Running" },
    });
  }

  private normalizeToolStart(
    toolData: JsonObject,
    state: NormalizationState,
    toolCallId: string,
    name: string
  ): NormalizedFrame {
    const imageSource = OpenClawConnector.toolImageSource(toolData);
    if (imageSource) state.imageSources.set(toolCallId, imageSource);
    const events: ConnectorEvent[] = [];
    OpenClawConnector.ensureToolStarted(events, state, toolCallId, name);
    return { events, terminal: false };
  }

  private async appendToolImageArtifact(
    events: ConnectorEvent[],
    state: NormalizationState,
    toolCallId: string,
    signal: AbortSignal
  ): Promise<void> {
    const imageSource = state.imageSources.get(toolCallId);
    if (
      !imageSource ||
      state.materializedImageSources.has(imageSource.source)
    ) {
      return;
    }
    const source = await this.readManagedImage(
      imageSource.source,
      imageSource.mimeType,
      "VSS snapshot",
      this.managedImageRecoverySignal(signal)
    );
    if (!source) return;
    state.materializedImageSources.add(imageSource.source);
    events.push({ type: "artifact.source", data: { source } });
  }

  private async normalizeToolEnd(
    toolData: JsonObject,
    state: NormalizationState,
    toolCallId: string,
    name: string,
    phase: string,
    signal: AbortSignal
  ): Promise<NormalizedFrame> {
    state.completedTools.add(toolCallId);
    const events: ConnectorEvent[] = [];
    OpenClawConnector.ensureToolStarted(events, state, toolCallId, name);
    if (TOOL_FAILURE_PHASES.has(phase) || toolData.isError === true) {
      events.push({
        type: "tool.failed",
        data: {
          tool_call_id: toolCallId,
          name,
          error: "Tool failed in OpenClaw",
        },
      });
      return { events, terminal: false };
    }

    const data: JsonObject = {
      tool_call_id: toolCallId,
      name,
      payload: "Completed",
    };
    if (toolData.result !== undefined) data._artifact_source = toolData.result;
    events.push({ type: "tool.completed", data });
    await this.appendToolImageArtifact(events, state, toolCallId, signal);
    return { events, terminal: false };
  }

  private async normalizeToolEvent(
    toolData: JsonObject,
    payload: JsonObject,
    state: NormalizationState,
    signal: AbortSignal
  ): Promise<NormalizedFrame> {
    const fallbackId = `tool-${sequenceText(payload.seq)}`;
    const toolCallId = OpenClawConnector.safeIdentifier(
      toolData.toolCallId || toolData.id,
      fallbackId
    );
    const name = OpenClawConnector.safeIdentifier(
      toolData.name || toolData.tool,
      state.toolNames.get(toolCallId) || "Agent tool"
    );
    state.toolNames.set(toolCallId, name);
    const phase = (
      asString(toolData.phase) ??
      asString(toolData.status) ??
      "start"
    ).toLowerCase();

    if (TOOL_START_PHASES.has(phase)) {
      return this.normalizeToolStart(toolData, state, toolCallId, name);
    }
    if (
      TOOL_PROGRESS_PHASES.has(phase) ||
      !TOOL_END_PHASES.has(phase) ||
      state.completedTools.has(toolCallId)
    ) {
      return OpenClawConnector.emptyNormalizedFrame();
    }
    return this.normalizeToolEnd(
      toolData,
      state,
      toolCallId,
      name,
      phase,
      signal
    );
  }

  async normalizeEvent(
    frame: JsonObject,
    state: NormalizationState,
    signal: AbortSignal
  ): Promise<NormalizedFrame> {
    if (frame.type !== "event" || !isJsonObject(frame.payload)) {
      return OpenClawConnector.emptyNormalizedFrame();
    }
    const payload = frame.payload;
    if (payload.sessionKey !== state.sessionKey) {
      return OpenClawConnector.emptyNormalizedFrame();
    }
    if (
      typeof payload.runId === "string" &&
      payload.runId !== state.upstreamRunId
    ) {
      return OpenClawConnector.emptyNormalizedFrame();
    }
    if (frame.event === "chat") {
      return this.normalizeChatEvent(payload, state, signal);
    }

    const toolData = OpenClawConnector.toolData(frame, payload);
    if (!toolData) return OpenClawConnector.emptyNormalizedFrame();
    return this.normalizeToolEvent(toolData, payload, state, signal);
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
        imageSources: new Map(),
        materializedImageSources: new Set(),
        sawText: false,
      };
      while (!signal.aborted) {
        const frame =
          pendingEvents.shift() ?? (await this.receive(socket, signal));
        const normalized = await this.normalizeEvent(frame, state, signal);
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

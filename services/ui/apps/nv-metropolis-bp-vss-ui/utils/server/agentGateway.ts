// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { NextApiRequest, NextApiResponse } from "next";

const MAX_SSE_BUFFER_LENGTH = 2_000_000;
const GATEWAY_PROTOCOL_MAJOR = "1";

type JsonObject = Record<string, unknown>;

export interface GatewayEvent {
  protocol_version: string;
  id: string;
  type: string;
  run_id: string;
  thread_id: string;
  data: JsonObject;
}

interface GatewayConfig {
  baseUrl: string;
  token?: string;
}

interface CreateRunResponse {
  run_id: string;
  status: string;
  events_url: string;
  cancel_url: string;
}

interface ChatMessage {
  role: "system" | "developer" | "user" | "assistant";
  content: string;
}

class GatewayProtocolError extends Error {}

export interface LegacyEventState {
  toolArguments: Map<string, string>;
  reasoning: string;
}

export const createLegacyEventState = (): LegacyEventState => ({
  toolArguments: new Map<string, string>(),
  reasoning: "",
});

const asString = (value: unknown): string | undefined =>
  typeof value === "string" ? value : undefined;

const serializedPayload = (value: unknown): string => {
  if (typeof value === "string") return value;
  if (value === undefined || value === null) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
};

const intermediateChunk = (
  event: GatewayEvent,
  options: { id: string; name: string; status: string; payload: unknown }
): string => {
  const intermediate = {
    id: options.id,
    status: options.status,
    type: "system_intermediate",
    parent_id: "default",
    content: {
      name: options.name,
      payload: serializedPayload(options.payload),
    },
    time_stamp: new Date().toISOString(),
    index: Number.parseInt(event.id, 10) || 0,
  };
  // A backend string must not be able to terminate the wrapper tag consumed by the legacy renderer.
  const json = JSON.stringify(intermediate).replace(/</g, "\\u003c");
  return `<intermediatestep>${json}</intermediatestep>`;
};

/**
 * Temporary presentation adapter for the existing chat renderer. The browser-facing
 * `/api/agent/*` route remains fully structured; this is removed when the renderer
 * consumes GatewayEvent directly.
 */
export const gatewayEventToLegacyChunks = (
  event: GatewayEvent,
  state: LegacyEventState
): string[] => {
  const data = event.data || {};
  if (event.type === "message.delta") {
    const delta = asString(data.delta);
    return delta ? [delta] : [];
  }

  if (event.type === "reasoning.delta") {
    const delta = asString(data.delta) || "";
    state.reasoning += delta;
    return [
      intermediateChunk(event, {
        id: `reasoning-${event.run_id}`,
        name: "Reasoning",
        status: "in_progress",
        payload: state.reasoning,
      }),
    ];
  }

  if (event.type.startsWith("tool.")) {
    const id = asString(data.tool_call_id) || `tool-${event.id}`;
    const name = asString(data.name) || "Agent tool";
    if (event.type === "tool.arguments.delta") {
      const argumentsDelta = asString(data.delta) || "";
      state.toolArguments.set(
        id,
        (state.toolArguments.get(id) || "") + argumentsDelta
      );
    } else if (typeof data.arguments === "string") {
      state.toolArguments.set(id, data.arguments);
    }
    const status =
      event.type === "tool.failed"
        ? "failed"
        : event.type === "tool.completed"
        ? "complete"
        : event.type === "tool.requested"
        ? "waiting"
        : "in_progress";
    const payload =
      data.error ??
      data.output ??
      data.payload ??
      state.toolArguments.get(id) ??
      "";
    return [intermediateChunk(event, { id, name, status, payload })];
  }

  if (event.type === "artifact.created") {
    return [
      intermediateChunk(event, {
        id: asString(data.artifact_id) || `artifact-${event.id}`,
        name: asString(data.name) || "Artifact created",
        status: "complete",
        payload: data,
      }),
    ];
  }

  if (event.type === "interaction.required") {
    return [
      intermediateChunk(event, {
        id: asString(data.interaction_id) || `interaction-${event.id}`,
        name: "Agent input required",
        status: "waiting",
        payload: data,
      }),
    ];
  }

  if (event.type === "run.failed") {
    const error = data.error;
    const message =
      error && typeof error === "object"
        ? asString((error as JsonObject).message)
        : undefined;
    return [
      `\n\n**Agent run failed:** ${
        message || "The backend could not complete this request."
      }`,
    ];
  }
  return [];
};

export class GatewaySseDecoder {
  private buffer = "";

  push(chunk: string): GatewayEvent[] {
    this.buffer = (this.buffer + chunk).replace(/\r\n/g, "\n");
    if (this.buffer.length > MAX_SSE_BUFFER_LENGTH) {
      throw new GatewayProtocolError(
        "Agent gateway emitted an oversized SSE frame"
      );
    }
    const frames = this.buffer.split("\n\n");
    this.buffer = frames.pop() || "";
    return frames.flatMap((frame) => this.parseFrame(frame));
  }

  finish(): GatewayEvent[] {
    const finalFrame = this.buffer;
    this.buffer = "";
    return finalFrame ? this.parseFrame(finalFrame) : [];
  }

  private parseFrame(frame: string): GatewayEvent[] {
    const data = frame
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).replace(/^ /, ""))
      .join("\n");
    if (!data || data === "[DONE]") return [];
    let parsed: unknown;
    try {
      parsed = JSON.parse(data);
    } catch {
      throw new GatewayProtocolError("Agent gateway emitted invalid SSE JSON");
    }
    if (!isGatewayEvent(parsed)) {
      throw new GatewayProtocolError(
        "Agent gateway emitted an invalid protocol event"
      );
    }
    return [parsed];
  }
}

export const isGatewayEvent = (value: unknown): value is GatewayEvent => {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<GatewayEvent>;
  return (
    typeof candidate.protocol_version === "string" &&
    candidate.protocol_version.split(".")[0] === GATEWAY_PROTOCOL_MAJOR &&
    typeof candidate.id === "string" &&
    typeof candidate.type === "string" &&
    typeof candidate.run_id === "string" &&
    typeof candidate.thread_id === "string" &&
    !!candidate.data &&
    typeof candidate.data === "object" &&
    !Array.isArray(candidate.data)
  );
};

export const getAgentGatewayConfig = (
  environment: NodeJS.ProcessEnv = process.env
): GatewayConfig | null => {
  const raw = environment.AGENT_GATEWAY_URL?.trim();
  if (!raw) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("AGENT_GATEWAY_URL must be an absolute URL");
  }
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password
  ) {
    throw new Error(
      "AGENT_GATEWAY_URL must be an http(s) URL without embedded credentials"
    );
  }
  if (url.search || url.hash) {
    throw new Error("AGENT_GATEWAY_URL must not contain a query or fragment");
  }
  return {
    baseUrl: url.toString().replace(/\/$/, ""),
    token: environment.AGENT_GATEWAY_TOKEN?.trim() || undefined,
  };
};

export const isAgentGatewayConfigured = (): boolean =>
  getAgentGatewayConfig() !== null;

const gatewayFetch = (
  config: GatewayConfig,
  path: string,
  init: RequestInit = {}
): Promise<Response> => {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json, text/event-stream");
  if (config.token) headers.set("Authorization", `Bearer ${config.token}`);
  return fetch(`${config.baseUrl}${path}`, { ...init, headers });
};

const responseError = async (response: Response): Promise<string> => {
  try {
    const body = (await response.json()) as JsonObject;
    const error = body.error;
    if (error && typeof error === "object") {
      return (
        asString((error as JsonObject).message) ||
        `Agent gateway returned HTTP ${response.status}`
      );
    }
  } catch {
    // Fall through to the status-only message; upstream bodies are not exposed verbatim.
  }
  return `Agent gateway returned HTTP ${response.status}`;
};

const cleanMessages = (value: unknown): ChatMessage[] => {
  if (!Array.isArray(value)) return [];
  const allowedRoles = new Set(["system", "developer", "user", "assistant"]);
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const role = (item as JsonObject).role;
    const content = (item as JsonObject).content;
    if (
      typeof role !== "string" ||
      !allowedRoles.has(role) ||
      typeof content !== "string"
    )
      return [];
    return [{ role: role as ChatMessage["role"], content }];
  });
};

const streamRun = async (
  config: GatewayConfig,
  runId: string,
  signal: AbortSignal,
  onEvent: (event: GatewayEvent) => void
): Promise<boolean> => {
  let lastEventId = "";
  let terminal = false;
  // A reconnect is safe because event IDs are replayed by the gateway.
  for (let attempt = 0; attempt < 3 && !terminal; attempt += 1) {
    try {
      const headers: Record<string, string> = { Accept: "text/event-stream" };
      if (lastEventId) headers["Last-Event-ID"] = lastEventId;
      const response = await gatewayFetch(
        config,
        `/v1/runs/${encodeURIComponent(runId)}/events`,
        {
          method: "GET",
          headers,
          signal,
        }
      );
      if (!response.ok || !response.body)
        throw new GatewayProtocolError(await responseError(response));
      const decoder = new TextDecoder();
      const sse = new GatewaySseDecoder();
      const reader = response.body.getReader();
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          for (const event of sse.push(
            decoder.decode(value, { stream: true })
          )) {
            if (event.run_id !== runId)
              throw new GatewayProtocolError(
                "Agent gateway emitted an event for the wrong run"
              );
            lastEventId = event.id;
            terminal =
              event.type === "run.completed" ||
              event.type === "run.failed" ||
              event.type === "run.cancelled";
            onEvent(event);
          }
        }
        for (const event of sse.push(decoder.decode())) {
          if (event.run_id !== runId)
            throw new GatewayProtocolError(
              "Agent gateway emitted an event for the wrong run"
            );
          lastEventId = event.id;
          terminal =
            event.type === "run.completed" ||
            event.type === "run.failed" ||
            event.type === "run.cancelled";
          onEvent(event);
        }
        for (const event of sse.finish()) {
          if (event.run_id !== runId)
            throw new GatewayProtocolError(
              "Agent gateway emitted an event for the wrong run"
            );
          lastEventId = event.id;
          terminal =
            event.type === "run.completed" ||
            event.type === "run.failed" ||
            event.type === "run.cancelled";
          onEvent(event);
        }
      } finally {
        reader.releaseLock();
      }
    } catch (error) {
      if (
        signal.aborted ||
        error instanceof GatewayProtocolError ||
        attempt === 2
      )
        throw error;
    }
  }
  return terminal;
};

const cancelRun = async (
  config: GatewayConfig,
  runId: string
): Promise<void> => {
  try {
    await gatewayFetch(config, `/v1/runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
  } catch {
    // The browser is already gone; the gateway also bounds orphaned runs upstream.
  }
};

export const agentGatewayChatHandler = async (
  req: NextApiRequest,
  res: NextApiResponse
): Promise<void> => {
  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    res.status(405).json({
      error: { code: "method_not_allowed", message: "Method not allowed" },
    });
    return;
  }
  const config = getAgentGatewayConfig();
  if (!config) {
    res.status(503).json({
      error: {
        code: "gateway_not_configured",
        message: "Agent gateway is not configured",
      },
    });
    return;
  }

  const messages = cleanMessages(req.body?.messages);
  const lastUserMessage = [...messages]
    .reverse()
    .find((message) => message.role === "user");
  if (!lastUserMessage) {
    res.status(400).json({
      error: {
        code: "invalid_request",
        message: "A user message is required",
      },
    });
    return;
  }
  const threadId =
    typeof req.headers["conversation-id"] === "string"
      ? req.headers["conversation-id"]
      : "";
  if (!threadId) {
    res.status(400).json({
      error: {
        code: "invalid_request",
        message: "Conversation-Id is required",
      },
    });
    return;
  }

  const controller = new AbortController();
  let runId: string | undefined;
  let terminal = false;
  const onDisconnect = (): void => {
    if (!terminal && !res.writableEnded) {
      controller.abort();
      if (runId) void cancelRun(config, runId);
    }
  };
  req.once("aborted", onDisconnect);
  res.once("close", onDisconnect);

  try {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    const messageId = req.headers["user-message-id"];
    if (typeof messageId === "string" && messageId)
      headers["Idempotency-Key"] = messageId;
    const createResponse = await gatewayFetch(config, "/v1/runs", {
      method: "POST",
      headers,
      signal: controller.signal,
      body: JSON.stringify({
        thread_id: threadId,
        input: [lastUserMessage],
        // A one-message body means the existing UI disabled client-side history.
        // Omit recovery history so the gateway can continue its saved response chain.
        history: messages.length > 1 ? messages : [],
        surface: "vss-ui",
      }),
    });
    if (!createResponse.ok) {
      res.status(createResponse.status).json({
        error: {
          code: "gateway_run_rejected",
          message: await responseError(createResponse),
        },
      });
      return;
    }
    const created = (await createResponse.json()) as CreateRunResponse;
    if (!created.run_id || typeof created.run_id !== "string") {
      throw new Error("Agent gateway returned an invalid run");
    }
    runId = created.run_id;

    res.status(200);
    res.setHeader("Content-Type", "text/plain; charset=utf-8");
    res.setHeader("Cache-Control", "no-cache, no-transform");
    res.setHeader("X-Accel-Buffering", "no");
    res.flushHeaders?.();

    const legacyState = createLegacyEventState();
    terminal = await streamRun(config, runId, controller.signal, (event) => {
      for (const chunk of gatewayEventToLegacyChunks(event, legacyState)) {
        res.write(chunk);
      }
    });
    if (!terminal)
      throw new Error(
        "Agent gateway event stream ended before a terminal event"
      );
    res.end();
  } catch (error) {
    if (controller.signal.aborted) return;
    const message =
      error instanceof Error ? error.message : "Agent gateway request failed";
    if (!res.headersSent) {
      res.status(502).json({ error: { code: "gateway_unavailable", message } });
    } else {
      res.write(`\n\n**Agent gateway error:** ${message}`);
      res.end();
    }
    if (runId && !terminal) void cancelRun(config, runId);
  } finally {
    req.off("aborted", onDisconnect);
    res.off("close", onDisconnect);
  }
};

const allowedProxyRequest = (method: string, segments: string[]): boolean => {
  if (
    method === "GET" &&
    segments.length === 1 &&
    segments[0] === "capabilities"
  )
    return true;
  if (method === "POST" && segments.length === 1 && segments[0] === "runs")
    return true;
  if (segments[0] !== "runs" || !/^run_[A-Za-z0-9_-]+$/.test(segments[1] || ""))
    return false;
  if (method === "GET" && segments.length === 2) return true;
  if (method === "GET" && segments.length === 3 && segments[2] === "events")
    return true;
  return (
    method === "POST" &&
    segments.length === 3 &&
    ["cancel", "respond"].includes(segments[2])
  );
};

export const agentGatewayProxyHandler = async (
  req: NextApiRequest,
  res: NextApiResponse
): Promise<void> => {
  const config = getAgentGatewayConfig();
  if (!config) {
    res.status(503).json({
      error: {
        code: "gateway_not_configured",
        message: "Agent gateway is not configured",
      },
    });
    return;
  }
  const segments = Array.isArray(req.query.path)
    ? req.query.path
    : typeof req.query.path === "string"
    ? [req.query.path]
    : [];
  const method = req.method || "";
  if (!allowedProxyRequest(method, segments)) {
    res.status(404).json({
      error: { code: "not_found", message: "Agent gateway route not found" },
    });
    return;
  }

  const controller = new AbortController();
  const onClose = (): void => {
    if (!res.writableEnded) controller.abort();
  };
  res.once("close", onClose);
  try {
    const headers: Record<string, string> = {};
    if (typeof req.headers["last-event-id"] === "string")
      headers["Last-Event-ID"] = req.headers["last-event-id"];
    if (typeof req.headers["idempotency-key"] === "string")
      headers["Idempotency-Key"] = req.headers["idempotency-key"];
    let body: string | undefined;
    if (method === "POST") {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(req.body ?? {});
    }
    const after =
      typeof req.query.after === "string" && /^\d+$/.test(req.query.after)
        ? `?after=${req.query.after}`
        : "";
    const upstream = await gatewayFetch(
      config,
      `/v1/${segments.map(encodeURIComponent).join("/")}${after}`,
      {
        method,
        headers,
        body,
        signal: controller.signal,
      }
    );
    res.status(upstream.status);
    for (const name of [
      "content-type",
      "cache-control",
      "x-accel-buffering",
      "idempotency-replayed",
    ]) {
      const value = upstream.headers.get(name);
      if (value) res.setHeader(name, value);
    }

    if (method === "POST" && segments.length === 1 && segments[0] === "runs") {
      const payload = (await upstream.json()) as JsonObject;
      if (upstream.ok && typeof payload.run_id === "string") {
        payload.events_url = `/api/agent/runs/${encodeURIComponent(
          payload.run_id
        )}/events`;
        payload.cancel_url = `/api/agent/runs/${encodeURIComponent(
          payload.run_id
        )}/cancel`;
      }
      res.json(payload);
      return;
    }
    if (!upstream.body) {
      res.end();
      return;
    }
    res.flushHeaders?.();
    const reader = upstream.body.getReader();
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        res.write(Buffer.from(value));
      }
    } finally {
      reader.releaseLock();
    }
    res.end();
  } catch (error) {
    if (controller.signal.aborted) return;
    const message =
      error instanceof Error ? error.message : "Agent gateway proxy failed";
    if (!res.headersSent) {
      res.status(502).json({ error: { code: "gateway_unavailable", message } });
    } else {
      res.end();
    }
  } finally {
    res.off("close", onClose);
  }
};

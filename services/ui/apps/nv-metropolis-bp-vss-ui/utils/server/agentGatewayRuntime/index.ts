// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  ConfigError,
  loadEmbeddedGatewayConfig,
  embeddedGatewayConfigured,
} from "./config";
import {
  ContractError,
  createRunEvent,
  parseCreateRunRequest,
  runEventSse,
  type RunEvent,
} from "./contract";
import { strictJsonParse } from "./json";
import { EmbeddedGatewayService } from "./service";
import {
  EventsExpiredError,
  IdempotencyConflictError,
  RunNotFoundError,
  type RunRecord,
  StoreCapacityError,
  ThreadBusyError,
} from "./store";
import type { NextApiRequest, NextApiResponse } from "next";
import { createHash } from "node:crypto";

interface CachedService {
  fingerprint: string;
  service: EmbeddedGatewayService;
}

declare global {
  // eslint-disable-next-line no-var
  var __vssEmbeddedAgentGateway: CachedService | undefined;
}

const CONFIG_ENV_KEYS = [
  "AGENT_BACKEND_PROTOCOL",
  "AGENT_BACKEND_URL",
  "AGENT_BACKEND_PATH",
  "AGENT_BACKEND_TOKEN",
  "AGENT_BACKEND_MODEL",
  "AGENT_BACKEND_SESSION_FIELD",
  "AGENT_BACKEND_SESSION_HEADER",
  "AGENT_BACKEND_HEADERS_JSON",
  "AGENT_BACKEND_TIMEOUT_SECONDS",
  "AGENT_REQUIRE_VSS_CAPABILITIES",
  "AGENT_VSS_CAPABILITIES_B64",
  "AGENT_VSS_CAPABILITIES_SHA256",
  "AGENT_EXPECTED_VSS_RUNTIME_REF",
  "AGENT_GATEWAY_RUN_RETENTION_SECONDS",
  "AGENT_GATEWAY_MAX_RUNS",
  "AGENT_GATEWAY_MAX_EVENTS_PER_RUN",
  "AGENT_GATEWAY_MAX_EVENT_CHARS_PER_RUN",
  "AGENT_GATEWAY_MAX_THREAD_STATE_CHARS",
];

const configFingerprint = (environment: NodeJS.ProcessEnv): string =>
  createHash("sha256")
    .update(
      CONFIG_ENV_KEYS.map((key) => `${key}\0${environment[key] ?? ""}`).join(
        "\0"
      )
    )
    .digest("hex");

export const getEmbeddedGatewayService = (
  environment: NodeJS.ProcessEnv = process.env
): EmbeddedGatewayService | null => {
  const config = loadEmbeddedGatewayConfig(environment);
  if (!config) return null;
  const fingerprint = configFingerprint(environment);
  if (globalThis.__vssEmbeddedAgentGateway?.fingerprint !== fingerprint) {
    globalThis.__vssEmbeddedAgentGateway = {
      fingerprint,
      service: new EmbeddedGatewayService(config),
    };
  }
  return globalThis.__vssEmbeddedAgentGateway.service;
};

export const resetEmbeddedGatewayForTests = (): void => {
  globalThis.__vssEmbeddedAgentGateway = undefined;
};

export { embeddedGatewayConfigured, ConfigError };

export async function* observeRunEvents(
  record: RunRecord,
  after: number,
  signal: AbortSignal
): AsyncGenerator<RunEvent | null> {
  let sequence = after;
  while (!signal.aborted) {
    const events = record.eventsAfter(sequence);
    if (events.length) {
      for (const event of events) {
        sequence = event.sequence;
        yield event;
      }
      if (record.terminal && sequence >= record.lastEventSequence) return;
      continue;
    }
    if (record.terminal) return;
    await record.waitForChange(signal);
    if (
      !signal.aborted &&
      !record.eventsAfter(sequence).length &&
      !record.terminal
    ) {
      yield null;
    }
  }
}

const securityHeaders = (res: NextApiResponse): void => {
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("Referrer-Policy", "no-referrer");
};

const errorResponse = (
  res: NextApiResponse,
  status: number,
  code: string,
  message: string,
  details: Record<string, unknown> = {}
): void => {
  securityHeaders(res);
  res.status(status).json({ error: { code, message, ...details } });
};

const pathSegments = (req: NextApiRequest): string[] => {
  if (Array.isArray(req.query.path)) return req.query.path;
  return typeof req.query.path === "string" ? [req.query.path] : [];
};

const singleHeader = (
  value: string | string[] | undefined
): string | undefined => (typeof value === "string" ? value : undefined);

const requestBody = (body: unknown): unknown => {
  if (typeof body !== "string") return body;
  try {
    return strictJsonParse(body);
  } catch {
    throw new ContractError("request body must be valid JSON");
  }
};

const createRun = (
  req: NextApiRequest,
  res: NextApiResponse,
  service: EmbeddedGatewayService
): void => {
  try {
    const request = parseCreateRunRequest(requestBody(req.body));
    const { record, replayed } = service.createRun(
      request,
      singleHeader(req.headers["idempotency-key"])
    );
    securityHeaders(res);
    if (replayed) res.setHeader("Idempotency-Replayed", "true");
    res.status(202).json({
      ...record.snapshot(),
      events_url: `/api/agent/runs/${encodeURIComponent(record.runId)}/events`,
      cancel_url: `/api/agent/runs/${encodeURIComponent(record.runId)}/cancel`,
    });
  } catch (error) {
    if (error instanceof ContractError || error instanceof TypeError) {
      errorResponse(res, 400, "invalid_request", error.message);
    } else if (error instanceof IdempotencyConflictError) {
      errorResponse(res, 409, "idempotency_key_conflict", error.message);
    } else if (error instanceof ThreadBusyError) {
      errorResponse(
        res,
        409,
        "thread_busy",
        "thread already has an active run",
        {
          active_run_id: error.runId,
        }
      );
    } else if (error instanceof StoreCapacityError) {
      errorResponse(res, 503, "run_capacity_reached", error.message);
    } else if (error instanceof Error) {
      errorResponse(res, 400, "invalid_request", error.message);
    } else {
      errorResponse(res, 400, "invalid_request", "request is invalid");
    }
  }
};

const streamEvents = async (
  req: NextApiRequest,
  res: NextApiResponse,
  record: RunRecord,
  after: number
): Promise<void> => {
  // Validate replay availability before committing an HTTP 200 response.
  record.eventsAfter(after);
  res.status(200);
  res.setHeader("Content-Type", "text/event-stream; charset=utf-8");
  res.setHeader("Cache-Control", "no-cache, no-transform");
  res.setHeader("X-Accel-Buffering", "no");
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.flushHeaders?.();

  const controller = new AbortController();
  const disconnect = (): void => controller.abort();
  req.once("aborted", disconnect);
  res.once("close", disconnect);
  let sequence = after;
  try {
    for await (const event of observeRunEvents(
      record,
      after,
      controller.signal
    )) {
      if (event) {
        sequence = event.sequence;
        res.write(runEventSse(event));
      } else {
        res.write(": keep-alive\n\n");
      }
    }
    if (!res.writableEnded) res.end();
  } catch (error) {
    if (error instanceof EventsExpiredError && !controller.signal.aborted) {
      const failure = createRunEvent(
        sequence + 1,
        "run.failed",
        record.runId,
        record.request.threadId,
        {
          error: {
            code: "events_expired",
            message: "requested events are no longer retained",
            retryable: false,
          },
        }
      );
      res.write(runEventSse(failure));
      res.end();
    } else if (!controller.signal.aborted && !res.writableEnded) {
      res.end();
    }
  } finally {
    req.off("aborted", disconnect);
    res.off("close", disconnect);
  }
};

export const embeddedAgentGatewayHandler = async (
  req: NextApiRequest,
  res: NextApiResponse
): Promise<void> => {
  let service: EmbeddedGatewayService | null;
  try {
    service = getEmbeddedGatewayService();
  } catch (error) {
    const message =
      error instanceof ConfigError
        ? error.message
        : "embedded agent gateway configuration is invalid";
    errorResponse(res, 503, "gateway_not_configured", message);
    return;
  }
  if (!service) {
    errorResponse(
      res,
      503,
      "gateway_not_configured",
      "embedded agent gateway is not configured"
    );
    return;
  }
  const segments = pathSegments(req);
  const method = req.method ?? "";

  if (
    method === "GET" &&
    segments.length === 1 &&
    segments[0] === "capabilities"
  ) {
    securityHeaders(res);
    res.status(200).json(service.capabilities());
    return;
  }
  if (method === "POST" && segments.length === 1 && segments[0] === "runs") {
    createRun(req, res, service);
    return;
  }
  if (segments[0] !== "runs" || segments.length < 2) {
    errorResponse(res, 404, "not_found", "agent gateway route not found");
    return;
  }
  const runId = segments[1];
  let record: RunRecord;
  try {
    record = service.store.get(runId);
  } catch (error) {
    if (error instanceof RunNotFoundError) {
      errorResponse(
        res,
        404,
        "run_not_found",
        "run does not exist or has expired"
      );
      return;
    }
    throw error;
  }

  if (method === "GET" && segments.length === 2) {
    securityHeaders(res);
    res.status(200).json(record.snapshot());
    return;
  }
  if (method === "GET" && segments.length === 3 && segments[2] === "events") {
    const rawAfter =
      singleHeader(req.headers["last-event-id"]) ??
      (typeof req.query.after === "string" ? req.query.after : "0");
    if (!/^\d+$/u.test(rawAfter)) {
      errorResponse(
        res,
        400,
        "invalid_event_id",
        "Last-Event-ID must be a non-negative integer"
      );
      return;
    }
    const after = Number(rawAfter);
    if (!Number.isSafeInteger(after)) {
      errorResponse(
        res,
        400,
        "invalid_event_id",
        "Last-Event-ID must be a non-negative safe integer"
      );
      return;
    }
    try {
      await streamEvents(req, res, record, after);
    } catch (error) {
      if (error instanceof EventsExpiredError) {
        errorResponse(
          res,
          410,
          "events_expired",
          "requested events are no longer retained"
        );
      } else {
        throw error;
      }
    }
    return;
  }
  if (method === "POST" && segments.length === 3 && segments[2] === "cancel") {
    await service.cancelRun(runId);
    securityHeaders(res);
    res.status(202).json(record.snapshot());
    return;
  }
  if (method === "POST" && segments.length === 3 && segments[2] === "respond") {
    errorResponse(
      res,
      409,
      "interaction_not_supported",
      "the active connector does not support interaction responses"
    );
    return;
  }
  errorResponse(res, 404, "not_found", "agent gateway route not found");
};

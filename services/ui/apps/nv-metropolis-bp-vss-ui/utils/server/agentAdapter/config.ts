// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { strictJsonParse, isJsonObject } from "./json";

export type BackendProtocol = "openclaw-ws" | "responses" | "legacy-chat";

export interface AgentAdapterConfig {
  backendProtocol: BackendProtocol;
  backendUrl: string;
  backendPath: string;
  backendToken?: string;
  backendModel: string;
  backendSessionField?: string;
  backendSessionHeader?: string;
  backendHeaders: Record<string, string>;
  requestTimeoutMs: number;
  runRetentionMs: number;
  maxRuns: number;
  maxEventsPerRun: number;
  maxEventCharsPerRun: number;
  maxThreadStateChars: number;
  maxRetainedChars: number;
}

const SUPPORTED_PROTOCOLS = new Set<BackendProtocol>([
  "openclaw-ws",
  "responses",
  "legacy-chat",
]);
const RESERVED_UPSTREAM_HEADERS = new Set([
  "accept",
  "authorization",
  "connection",
  "content-length",
  "content-type",
  "host",
  "keep-alive",
  "origin",
  "proxy-authenticate",
  "proxy-authorization",
  "sec-websocket-extensions",
  "sec-websocket-key",
  "sec-websocket-protocol",
  "sec-websocket-version",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "user-agent",
]);
const RESERVED_RESPONSE_FIELDS = new Set([
  "input",
  "instructions",
  "model",
  "previous_response_id",
  "store",
  "stream",
]);
const HEADER_NAME = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]{1,128}$/u;

export class ConfigError extends Error {}

const boolEnv = (
  environment: NodeJS.ProcessEnv,
  name: string,
  fallback: boolean
): boolean => {
  const raw = environment[name];
  if (raw === undefined) return fallback;
  const normalized = raw.trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(normalized)) return true;
  if (["0", "false", "no", "off"].includes(normalized)) return false;
  throw new ConfigError(`${name} must be true or false`);
};

const numberEnv = (
  environment: NodeJS.ProcessEnv,
  name: string,
  fallback: number,
  minimum: number,
  maximum: number,
  integer = true
): number => {
  const raw = environment[name];
  if (raw === undefined) return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value) || (integer && !Number.isInteger(value))) {
    throw new ConfigError(
      `${name} must be ${integer ? "an integer" : "a number"}`
    );
  }
  if (value < minimum || value > maximum) {
    throw new ConfigError(`${name} must be between ${minimum} and ${maximum}`);
  }
  return value;
};

const validateUrl = (
  value: string,
  name: string,
  protocols: string[]
): string => {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new ConfigError(`${name} must be an absolute URL`);
  }
  if (!protocols.includes(parsed.protocol) || !parsed.hostname) {
    throw new ConfigError(`${name} must use ${protocols.join(" or ")}`);
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new ConfigError(
      `${name} must not contain credentials, a query, or a fragment`
    );
  }
  let end = value.length;
  while (end > 0 && value[end - 1] === "/") end -= 1;
  return value.slice(0, end);
};

const extraHeaders = (
  environment: NodeJS.ProcessEnv
): Record<string, string> => {
  let parsed: unknown;
  try {
    parsed = strictJsonParse(environment.AGENT_BACKEND_HEADERS_JSON ?? "{}");
  } catch {
    throw new ConfigError("AGENT_BACKEND_HEADERS_JSON must be valid JSON");
  }
  if (!isJsonObject(parsed)) {
    throw new ConfigError("AGENT_BACKEND_HEADERS_JSON must be a JSON object");
  }
  const headers: Record<string, string> = {};
  for (const [name, value] of Object.entries(parsed)) {
    if (typeof value !== "string") {
      throw new ConfigError(
        "AGENT_BACKEND_HEADERS_JSON values must be strings"
      );
    }
    if (!HEADER_NAME.test(name)) {
      throw new ConfigError(
        "AGENT_BACKEND_HEADERS_JSON contains an invalid HTTP header name"
      );
    }
    if (RESERVED_UPSTREAM_HEADERS.has(name.toLowerCase())) {
      throw new ConfigError(
        `AGENT_BACKEND_HEADERS_JSON cannot override ${name}`
      );
    }
    if (
      value.length > 8_192 ||
      [...value].some((character) => {
        const code = character.codePointAt(0) ?? 0;
        return code < 32 || code > 126;
      })
    ) {
      throw new ConfigError(
        "AGENT_BACKEND_HEADERS_JSON contains an invalid HTTP header value"
      );
    }
    headers[name] = value;
  }
  return headers;
};

export const agentAdapterConfigured = (
  environment: NodeJS.ProcessEnv = process.env
): boolean => {
  const rawEnabled = environment.AGENT_ADAPTER_ENABLED;
  if (rawEnabled === undefined) return !!environment.AGENT_BACKEND_URL?.trim();
  return !["0", "false", "no", "off", ""].includes(
    rawEnabled.trim().toLowerCase()
  );
};

export const loadAgentAdapterConfig = (
  environment: NodeJS.ProcessEnv = process.env
): AgentAdapterConfig | null => {
  const rawUrl = environment.AGENT_BACKEND_URL?.trim();
  const enabled = boolEnv(environment, "AGENT_ADAPTER_ENABLED", !!rawUrl);
  if (!enabled) return null;
  if (!rawUrl) {
    throw new ConfigError(
      "AGENT_BACKEND_URL is required when AGENT_ADAPTER_ENABLED=true"
    );
  }
  const rawProtocol = (environment.AGENT_BACKEND_PROTOCOL ?? "responses")
    .trim()
    .toLowerCase();
  if (!SUPPORTED_PROTOCOLS.has(rawProtocol as BackendProtocol)) {
    throw new ConfigError(
      `AGENT_BACKEND_PROTOCOL must be one of: ${[...SUPPORTED_PROTOCOLS]
        .sort((left, right) => left.localeCompare(right))
        .join(", ")}`
    );
  }
  const backendProtocol = rawProtocol as BackendProtocol;
  const backendUrl = validateUrl(
    rawUrl,
    "AGENT_BACKEND_URL",
    backendProtocol === "openclaw-ws" ? ["ws:", "wss:"] : ["http:", "https:"]
  );
  const defaultPath = {
    "openclaw-ws": "/",
    responses: "/v1/responses",
    "legacy-chat": "/chat/stream",
  }[backendProtocol];
  const backendPath = environment.AGENT_BACKEND_PATH?.trim() || defaultPath;
  if (!backendPath.startsWith("/") || /[?#]/u.test(backendPath)) {
    throw new ConfigError(
      "AGENT_BACKEND_PATH must be an absolute URL path without query or fragment"
    );
  }
  const sessionFieldRaw =
    environment.AGENT_BACKEND_SESSION_FIELD === undefined
      ? "user"
      : environment.AGENT_BACKEND_SESSION_FIELD.trim();
  const backendSessionField = sessionFieldRaw || undefined;
  if (
    backendSessionField &&
    (backendSessionField.length > 128 ||
      /\p{Cc}/u.test(backendSessionField) ||
      RESERVED_RESPONSE_FIELDS.has(backendSessionField))
  ) {
    throw new ConfigError(
      "AGENT_BACKEND_SESSION_FIELD is invalid or would override a Responses field"
    );
  }
  const sessionHeaderRaw =
    environment.AGENT_BACKEND_SESSION_HEADER?.trim() ?? "";
  const backendSessionHeader = sessionHeaderRaw || undefined;
  if (
    backendSessionHeader &&
    (!HEADER_NAME.test(backendSessionHeader) ||
      RESERVED_UPSTREAM_HEADERS.has(backendSessionHeader.toLowerCase()))
  ) {
    throw new ConfigError(
      "AGENT_BACKEND_SESSION_HEADER is not a safe HTTP header name"
    );
  }
  const backendHeaders = extraHeaders(environment);
  if (backendProtocol === "openclaw-ws" && Object.keys(backendHeaders).length) {
    throw new ConfigError(
      "AGENT_BACKEND_HEADERS_JSON is unsupported with openclaw-ws"
    );
  }
  const maxEventsPerRun = numberEnv(
    environment,
    "AGENT_MAX_EVENTS_PER_RUN",
    10_000,
    100,
    100_000
  );
  const maxEventCharsPerRun = numberEnv(
    environment,
    "AGENT_MAX_EVENT_CHARS_PER_RUN",
    20_000_000,
    1_000_000,
    100_000_000
  );
  const maxThreadStateChars = numberEnv(
    environment,
    "AGENT_MAX_THREAD_STATE_CHARS",
    20_000_000,
    1_000_000,
    100_000_000
  );
  const maxRetainedChars = numberEnv(
    environment,
    "AGENT_MAX_RETAINED_CHARS",
    64_000_000,
    3_000_000,
    1_000_000_000
  );
  // The total budget is partitioned between Responses thread state and runs.
  // A run must be able to reserve its full event allowance; its serialized
  // request is checked against the remaining capacity when it is created.
  if (maxRetainedChars <= maxThreadStateChars + maxEventCharsPerRun) {
    throw new ConfigError(
      "AGENT_MAX_RETAINED_CHARS must exceed AGENT_MAX_THREAD_STATE_CHARS plus AGENT_MAX_EVENT_CHARS_PER_RUN"
    );
  }
  return {
    backendProtocol,
    backendUrl,
    backendPath,
    backendToken: environment.AGENT_BACKEND_TOKEN?.trim() || undefined,
    backendModel: environment.AGENT_BACKEND_MODEL?.trim() || "agent",
    backendSessionField,
    backendSessionHeader,
    backendHeaders,
    requestTimeoutMs:
      numberEnv(
        environment,
        "AGENT_BACKEND_TIMEOUT_SECONDS",
        900,
        1,
        3_600,
        false
      ) * 1_000,
    runRetentionMs:
      numberEnv(environment, "AGENT_RUN_RETENTION_SECONDS", 3_600, 60, 86_400) *
      1_000,
    maxRuns: numberEnv(environment, "AGENT_MAX_RUNS", 1_000, 1, 10_000),
    maxEventsPerRun,
    maxEventCharsPerRun,
    maxThreadStateChars,
    maxRetainedChars,
  };
};
